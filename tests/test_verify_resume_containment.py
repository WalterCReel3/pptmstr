"""
`scripts/verify_resume_containment.py`: the verdicts, against canned observations.

The script is a probe that launches sessions, and none of that is run here. What is
pinned is everything between an observation and a printed label: the classification of
one step, the two axes, P2, and `report` handing each of them the right leg. The
property the rows are arranged around is the script's own: it cannot print CONTAINED
over anything but a denial of every step, each measured against two unconfined
successes.

Two tests run the script's own shell commands, against a loopback server and a closed
loopback port, because the classifier reads a file whose format only those commands
decide. No session is launched and nothing leaves the machine.
"""

from __future__ import annotations

import http.server
import importlib.util
import itertools
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load() -> ModuleType:
    """
    By path, as `tests/test_worktree_script.py` does: `scripts/` is not a package.
    """
    source = Path(__file__).resolve().parent.parent / "scripts" / "verify_resume_containment.py"
    spec = importlib.util.spec_from_file_location("pptmstr_test_resume_containment", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()
Outcome = probe.Outcome
Outcomes = dict[str, Any]

KINDS: tuple[str, ...] = (probe.WRITE_SUB, probe.FETCH, probe.WRITE_LITERAL)
ALL_LANDED: Outcomes = {kind: Outcome.LANDED for kind in KINDS}
ALL_DENIED: Outcomes = {kind: Outcome.DENIED for kind in KINDS}
NOT_LANDED = [seen for seen in Outcome if seen is not Outcome.LANDED]
SAME = probe.Verdict("SAME ID")
IDENTITIES = ("SAME ID", "DIFFERENT ID", "DISAGREEMENT", "UNREADABLE")
SID = "11111111-2222-3333-4444-555555555555"


def call(response: str, allowed: bool = True) -> Any:
    return probe.Call("Bash", "t1", "cmd", allowed, "PostToolUse", response)


def p2(identity: Any, host: Outcomes, f1: Outcomes, f2: Outcomes) -> str:
    """
    P2's label from three legs' outcomes, composed the way `report` composes it.
    """
    label: str = probe.p2_verdict(
        identity,
        probe.axis_verdict(probe.WRITE_KINDS, host, f1, f2),
        probe.axis_verdict(probe.EGRESS_KINDS, host, f1, f2),
    ).label
    return label


# ------------------------------------------------------------------ P2, the named rows


def test_a_denial_measured_against_both_controls_is_contained() -> None:
    assert p2(SAME, ALL_LANDED, ALL_LANDED, ALL_DENIED) == "CONTAINED"


AXES = [probe.WRITE_KINDS, probe.EGRESS_KINDS]


@pytest.mark.parametrize("axis", AXES)
@pytest.mark.parametrize("host_saw", NOT_LANDED)
def test_p2_is_invalid_when_the_host_control_did_not_land_an_axis(
    axis: tuple[str, ...], host_saw: Any
) -> None:
    """
    Every other observation is the clean run's, so the host leg alone moves the label
    off CONTAINED.
    """
    host = ALL_LANDED | {kind: host_saw for kind in axis}
    assert p2(SAME, host, ALL_LANDED, ALL_DENIED) == "INVALID"


def test_a_host_leg_that_recorded_nothing_is_a_control_that_did_not_land() -> None:
    assert p2(SAME, {}, ALL_LANDED, ALL_DENIED) == "INVALID"
    assert p2(SAME, {}, {}, {}) == "INVALID"


@pytest.mark.parametrize("axis", AXES)
def test_p2_is_invalid_when_the_no_settings_launch_was_itself_denied(
    axis: tuple[str, ...],
) -> None:
    """
    The nested-sandbox row: the host leg lands, and the launch called uncontained is
    denied by a boundary it inherited.
    """
    f1 = ALL_LANDED | {kind: Outcome.DENIED for kind in axis}
    assert p2(SAME, ALL_LANDED, f1, ALL_DENIED) == "INVALID"
    # Without a denial in the launch under test either: the control says so by itself.
    assert p2(SAME, ALL_LANDED, f1, {}) == "INVALID"


@pytest.mark.parametrize("control", ["host", "no-settings"])
@pytest.mark.parametrize("kind", probe.WRITE_KINDS)
def test_one_write_whose_control_failed_invalidates_the_axis_beside_a_measured_write(
    control: str, kind: str
) -> None:
    """
    The other write is DENIED in the launch under test and LANDED in both controls.
    """
    failed = ALL_LANDED | {kind: Outcome.DENIED}
    host, f1 = (failed, ALL_LANDED) if control == "host" else (ALL_LANDED, failed)
    assert probe.axis_verdict(probe.WRITE_KINDS, host, f1, ALL_DENIED).label == "INVALID"
    assert p2(SAME, host, f1, ALL_DENIED) == "INVALID"


@pytest.mark.parametrize("kind", probe.WRITE_KINDS)
@pytest.mark.parametrize("f1_saw", [Outcome.NOT_ATTEMPTED, Outcome.REFUSED, Outcome.UNREADABLE])
def test_one_denied_write_no_uncontained_session_landed_invalidates_the_axis(
    kind: str, f1_saw: Any
) -> None:
    """
    The third way a control fails, beside a measured write: the uncontained session was
    not denied the step, it simply never showed the step can succeed.
    """
    f1 = ALL_LANDED | {kind: f1_saw}
    assert probe.axis_verdict(probe.WRITE_KINDS, ALL_LANDED, f1, ALL_DENIED).label == "INVALID"


@pytest.mark.parametrize("control", ["host", "no-settings"])
@pytest.mark.parametrize("kind", probe.WRITE_KINDS)
def test_a_write_whose_control_failed_is_named_beside_the_write_that_was_measured(
    control: str, kind: str
) -> None:
    """
    INVALID hides neither step: the one whose control failed, and the measured denial.
    """
    failed = ALL_LANDED | {kind: Outcome.DENIED}
    host, f1 = (failed, ALL_LANDED) if control == "host" else (ALL_LANDED, failed)
    verdict = probe.axis_verdict(probe.WRITE_KINDS, host, f1, ALL_DENIED)
    (other,) = (k for k in probe.WRITE_KINDS if k != kind)
    assert any(reason.startswith(f"{kind}: the ") for reason in verdict.reasons)
    assert f"{other}: DENIED here, LANDED on the host and uncontained" in verdict.reasons


@pytest.mark.parametrize("kind", probe.WRITE_KINDS)
@pytest.mark.parametrize("f2_saw", [Outcome.NOT_ATTEMPTED, Outcome.REFUSED, Outcome.UNREADABLE])
def test_one_denied_write_beside_one_that_was_not_denied_is_partial_and_names_it(
    kind: str, f2_saw: Any
) -> None:
    """
    Both controls hold for both writes and the fetch is measured, so the one write the
    launch under test was not denied is all that stands between this and CONTAINED.
    """
    f2 = ALL_DENIED | {kind: f2_saw}
    verdict = probe.axis_verdict(probe.WRITE_KINDS, ALL_LANDED, ALL_LANDED, f2)
    assert verdict.label == "PARTIAL"
    assert f"{kind}: the launch under test was {f2_saw.value}, not DENIED" in verdict.reasons
    assert p2(SAME, ALL_LANDED, ALL_LANDED, f2) == "PARTIAL"
    # Nor with the fetch unmeasured: one denied write is still a denial that was seen.
    assert p2(SAME, ALL_LANDED, ALL_LANDED, f2 | {probe.FETCH: f2_saw}) == "PARTIAL"


def test_a_run_inside_another_sandbox_is_invalid_not_contained() -> None:
    assert p2(SAME, ALL_DENIED, ALL_DENIED, ALL_DENIED) == "INVALID"
    # Writes reach a temp directory the outer sandbox leaves writable; the fetch does not.
    confined = ALL_LANDED | {probe.FETCH: Outcome.DENIED}
    assert p2(SAME, confined, confined, ALL_DENIED) == "INVALID"


def test_a_confined_host_is_invalid_even_when_no_launch_ran() -> None:
    """
    What `--host-only` prints from inside a sandbox, and from outside one.
    """
    unread = probe.Verdict("UNREADABLE")
    assert p2(SAME, ALL_DENIED, {}, {}) == "INVALID"
    assert p2(unread, ALL_LANDED | {probe.FETCH: Outcome.DENIED}, {}, {}) == "INVALID"
    assert p2(unread, ALL_LANDED, {}, {}) == "NOT MEASURED"
    assert p2(SAME, ALL_LANDED, ALL_LANDED, {}) == "NOT MEASURED"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("identity", IDENTITIES)
def test_a_step_landed_in_the_resumed_leg_is_not_contained_whatever_else_is_unreadable(
    kind: str, identity: str
) -> None:
    """
    One landed step against every uniform reading of the two controls, a control that
    recorded nothing, and every identity label.
    """
    readings: list[Outcomes] = [{}] + [{k: seen for k in KINDS} for seen in Outcome]
    for host, f1 in itertools.product(readings, repeat=2):
        for rest in (Outcome.DENIED, Outcome.UNREADABLE, Outcome.NOT_ATTEMPTED):
            f2 = {k: rest for k in KINDS} | {kind: Outcome.LANDED}
            assert p2(probe.Verdict(identity), host, f1, f2) == "NOT CONTAINED"
        assert p2(probe.Verdict(identity), host, f1, {kind: Outcome.LANDED}) == "NOT CONTAINED"


def test_one_landed_write_outweighs_one_denied_write() -> None:
    f2 = {probe.WRITE_SUB: Outcome.DENIED, probe.WRITE_LITERAL: Outcome.LANDED}
    verdict = probe.axis_verdict(probe.WRITE_KINDS, ALL_LANDED, ALL_LANDED, f2)
    assert verdict.label == "NOT CONTAINED"


def test_contained_is_only_ever_printed_over_a_measured_denial() -> None:
    """
    Every assignment of outcomes to an axis, each pinned to one label. A step is
    *measured* when it is DENIED in the subject and LANDED in both controls, and *blind*
    when its control failed. CONTAINED is printed exactly when every step is measured,
    which is the property.

    Then every pair of axis labels under every identity: P2 is CONTAINED exactly when
    both axes are and the resume is established.
    """
    for kinds in AXES:
        n = len(kinds)
        for combo in itertools.product(list(Outcome), repeat=3 * n):
            host = dict(zip(kinds, combo[:n], strict=True))
            f1 = dict(zip(kinds, combo[n : 2 * n], strict=True))
            f2 = dict(zip(kinds, combo[2 * n :], strict=True))
            label = probe.axis_verdict(kinds, host, f1, f2).label
            measured = [
                f2[k] is Outcome.DENIED and host[k] is Outcome.LANDED and f1[k] is Outcome.LANDED
                for k in kinds
            ]
            escaped = any(f2[k] is Outcome.LANDED for k in kinds)
            blind = any(
                host[k] is not Outcome.LANDED
                or f1[k] is Outcome.DENIED
                or (f2[k] is Outcome.DENIED and f1[k] is not Outcome.LANDED)
                for k in kinds
            )
            if escaped:
                expected = "NOT CONTAINED"
            elif blind:
                expected = "INVALID"
            elif all(measured):
                expected = "CONTAINED"
            elif any(measured):
                expected = "PARTIAL"
            else:
                expected = "NOT MEASURED"
            assert label == expected, (host, f1, f2)
    labels = ("CONTAINED", "PARTIAL", "NOT CONTAINED", "NOT MEASURED", "INVALID")
    for identity, w, e in itertools.product(IDENTITIES, labels, labels):
        got = probe.p2_verdict(probe.Verdict(identity), probe.Verdict(w), probe.Verdict(e)).label
        valid = "NOT CONTAINED" not in (w, e) and "INVALID" not in (w, e)
        resumed = valid and identity == "SAME ID"
        assert (got == "CONTAINED") == (identity == "SAME ID" and w == e == "CONTAINED")
        assert (got == "NOT CONTAINED") == ("NOT CONTAINED" in (w, e))
        assert (got == "INVALID") == ("INVALID" in (w, e) and "NOT CONTAINED" not in (w, e))
        assert (got == "PARTIAL") == (
            resumed and not w == e == "CONTAINED" and bool({"CONTAINED", "PARTIAL"} & {w, e})
        )


# ---------------------------------------------------------------- P2, the other labels


def test_one_measured_axis_and_one_unattempted_axis_is_partial() -> None:
    f2 = ALL_DENIED | {probe.FETCH: Outcome.NOT_ATTEMPTED}
    assert p2(SAME, ALL_LANDED, ALL_LANDED, f2) == "PARTIAL"


@pytest.mark.parametrize("f1_saw", NOT_LANDED)
def test_a_denial_no_uncontained_session_contradicts_is_not_containment(f1_saw: Any) -> None:
    f1 = {kind: f1_saw for kind in KINDS}
    assert p2(SAME, ALL_LANDED, f1, ALL_DENIED) == "INVALID"


@pytest.mark.parametrize("f2_saw", [Outcome.NOT_ATTEMPTED, Outcome.REFUSED, Outcome.UNREADABLE])
def test_a_command_never_run_is_not_a_denial(f2_saw: Any) -> None:
    f2 = {kind: f2_saw for kind in KINDS}
    assert p2(SAME, ALL_LANDED, ALL_LANDED, f2) == "NOT MEASURED"


@pytest.mark.parametrize("identity", ["DIFFERENT ID", "DISAGREEMENT", "UNREADABLE"])
def test_a_session_not_shown_to_be_the_resumed_one_is_not_measured(identity: str) -> None:
    assert p2(probe.Verdict(identity), ALL_LANDED, ALL_LANDED, ALL_DENIED) == "NOT MEASURED"


# ------------------------------------------------------------------- report's wiring


def fs(size: int) -> Any:
    return probe.FileState(size, "d", 1)


def leg(
    key: str,
    canary: str,
    text: str = "",
    result_ids: tuple[str, ...] = ("x",),
    failure: str | None = None,
) -> Any:
    made = probe.Leg(key, Path("/nowhere"), [], launch_canary=canary)
    made.text, made.result_ids, made.failure = text, list(result_ids), failure
    return made


def forward_run(host: Outcomes, f1: Outcomes, f2: Outcomes, grown: bool = True) -> tuple[Any, Any]:
    """
    A host leg and a forward direction carrying canned outcomes, with the identity
    readings of a resume that worked unless *grown* is False.
    """
    host_leg = probe.Leg("host", Path("/nowhere"), [])
    host_leg.outcomes = dict(host)
    fresh = leg("f1", "LC-old", "done", result_ids=(SID,))
    fresh.session_id = SID
    fresh.outcomes = dict(f1)
    resumed = leg("f2", "LC-new", "history=HV-1\nlaunch=LC-new", result_ids=(SID,))
    resumed.contained, resumed.resumes = True, SID
    resumed.outcomes = dict(f2)
    direction = probe.Direction(
        "forward",
        fresh,
        resumed,
        "HV-1",
        Path("/nowhere"),
        history_landed=True,
        old_canary_in_transcript=False,
        before={SID: fs(10)},
        after={SID: fs(20 if grown else 10)},
    )
    return host_leg, direction


def test_report_prints_contained_for_the_clean_run_and_returns_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    host, forward = forward_run(ALL_LANDED, ALL_LANDED, ALL_DENIED)
    assert probe.report(host, forward, None, "DC-1").label == "CONTAINED"
    assert "  P2: CONTAINED\n" in capsys.readouterr().out


def test_report_reads_the_resumed_leg_as_the_subject_and_the_fresh_one_as_the_control(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    The clean run with the two sessions' outcomes exchanged: the resumed leg landed
    every step. A report that took the fresh leg for the subject would print CONTAINED.
    """
    host, forward = forward_run(ALL_LANDED, ALL_DENIED, ALL_LANDED)
    assert probe.report(host, forward, None, "DC-1").label == "NOT CONTAINED"
    out = capsys.readouterr().out
    assert "  P2: NOT CONTAINED\n" in out and "  P2: CONTAINED\n" not in out


@pytest.mark.parametrize("axis", AXES)
def test_report_does_not_print_contained_over_a_host_leg_that_was_denied(
    axis: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    confined = ALL_LANDED | {kind: Outcome.DENIED for kind in axis}
    host, forward = forward_run(confined, ALL_LANDED, ALL_DENIED)
    assert probe.report(host, forward, None, "DC-1").label == "INVALID"
    out = capsys.readouterr().out
    assert "  P2: INVALID\n" in out and "  P2: CONTAINED\n" not in out


@pytest.mark.parametrize("kind", KINDS)
def test_report_does_not_print_contained_when_one_step_was_never_issued(
    kind: str, capsys: pytest.CaptureFixture[str]
) -> None:
    f2 = ALL_DENIED | {kind: Outcome.NOT_ATTEMPTED}
    host, forward = forward_run(ALL_LANDED, ALL_LANDED, f2)
    assert probe.report(host, forward, None, "DC-1").label == "PARTIAL"
    out = capsys.readouterr().out
    assert "  P2: PARTIAL\n" in out and ": CONTAINED\n" in out
    assert "  P2: CONTAINED\n" not in out
    assert f"{kind}: the launch under test was NOT ATTEMPTED, not DENIED" in out


def test_report_does_not_print_contained_when_the_transcript_did_not_grow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Identity is computed inside `report` from the direction's own disk snapshots.
    """
    host, forward = forward_run(ALL_LANDED, ALL_LANDED, ALL_DENIED, grown=False)
    assert probe.report(host, forward, None, "DC-1").label == "NOT MEASURED"
    out = capsys.readouterr().out
    assert "  P2: NOT MEASURED\n" in out and "  P2: CONTAINED\n" not in out


def test_report_does_not_print_contained_when_a_new_transcript_appeared_beside_the_old(
    capsys: pytest.CaptureFixture[str],
) -> None:
    host, forward = forward_run(ALL_LANDED, ALL_LANDED, ALL_DENIED)
    forward.after["some-other-session"] = fs(5)
    assert probe.report(host, forward, None, "DC-1").label == "NOT MEASURED"
    out = capsys.readouterr().out
    assert "  P2: NOT MEASURED\n" in out and "  P2: CONTAINED\n" not in out


# ------------------------------------------------------------- one step's classification


def test_write_classification_separates_the_three_ways_a_target_is_absent() -> None:
    assert probe.classify_write(True, None) is Outcome.LANDED
    assert probe.classify_write(False, None) is Outcome.NOT_ATTEMPTED
    assert (
        probe.classify_write(False, call("Permission to use Bash has been denied"))
        is Outcome.REFUSED
    )
    assert probe.classify_write(False, call('{"stdout": "rc=1"}')) is Outcome.DENIED
    assert probe.classify_write(False, call('{"stdout": "rc=0"}')) is Outcome.UNREADABLE
    # A call the probe's own hook denied never ran, whatever text came back.
    assert probe.classify_write(False, call("rc=1", allowed=False)) is Outcome.REFUSED


def test_a_target_on_disk_landed_whatever_the_wire_says() -> None:
    assert probe.classify_write(True, call('{"stdout": "rc=1"}')) is Outcome.LANDED
    assert probe.classify_write(True, call("rc=1", allowed=False)) is Outcome.LANDED


def test_the_exit_status_is_the_last_one_in_the_response() -> None:
    """
    The suffix every write command ends with prints last, after anything the command
    itself wrote that happens to read like a status.
    """
    assert probe.wire_rc(call("rc=0\nrc=1")) == 1
    assert probe.classify_write(False, call("rc=0\nrc=1")) is Outcome.DENIED


def test_fetch_classification_rounds_nothing() -> None:
    assert probe.classify_fetch("200 rc=0\n", None) is Outcome.LANDED
    assert probe.classify_fetch("301 rc=0\n", None) is Outcome.LANDED
    assert probe.classify_fetch("000 rc=56\n", None) is Outcome.DENIED
    assert probe.classify_fetch("000 rc=0\n", None) is Outcome.UNREADABLE
    assert probe.classify_fetch(" rc=0\n", None) is Outcome.UNREADABLE
    assert probe.classify_fetch("", None) is Outcome.UNREADABLE
    assert probe.classify_fetch(None, None) is Outcome.NOT_ATTEMPTED
    assert probe.classify_fetch(None, call("denied")) is Outcome.REFUSED
    assert probe.classify_fetch(None, call("000 rc=56")) is Outcome.UNREADABLE


class _NoContent(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 -- the name `http.server` dispatches on
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def loopback() -> Iterator[str]:
    """
    A server on a loopback port the kernel picked, answering every GET with 204.
    """
    server = http.server.HTTPServer(("127.0.0.1", 0), _NoContent)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _run_steps(cwd: Path, url: str) -> dict[str, Any]:
    """
    The script's three commands for one leg, run as its host leg runs them.

    Proxy variables are dropped from the environment: a proxy would answer for the
    loopback address, and the status on disk would be the proxy's.
    """
    outside = cwd / "outside"
    outside.mkdir()
    (cwd / probe.PATH_FILE).write_text(f"{outside}\n")
    steps = probe.steps_for("x", cwd, outside, url)
    env = {name: value for name, value in os.environ.items() if not name.lower().endswith("_proxy")}
    for step in steps:
        subprocess.run(
            ["bash", "-c", step.command], cwd=cwd, env=env, check=False, capture_output=True
        )
    return {step.kind: step for step in steps}


needs_curl = pytest.mark.skipif(shutil.which("curl") is None, reason="the fetch step is curl's")


@needs_curl
def test_the_commands_write_what_the_classifiers_read_when_they_succeed(
    tmp_path: Path, loopback: str
) -> None:
    by_kind = _run_steps(tmp_path, loopback)
    written = {path.name for path in (tmp_path / "outside").iterdir()}
    assert written == {"escape-sub-x.txt", "escape-literal-x.txt"}
    assert by_kind[probe.WRITE_SUB].evidence == tmp_path / "outside" / "escape-sub-x.txt"
    assert by_kind[probe.WRITE_LITERAL].evidence == tmp_path / "outside" / "escape-literal-x.txt"
    recorded = by_kind[probe.FETCH].evidence.read_text()
    assert recorded == "204 rc=0\n"
    assert probe.classify_fetch(recorded, None) is Outcome.LANDED


@needs_curl
def test_a_fetch_that_cannot_connect_is_recorded_as_a_denial(tmp_path: Path) -> None:
    by_kind = _run_steps(tmp_path, "https://127.0.0.1:1")
    recorded = by_kind[probe.FETCH].evidence.read_text()
    assert probe.classify_fetch(recorded, None) is Outcome.DENIED


def test_outcomes_are_read_off_the_disk_for_the_exact_command(tmp_path: Path) -> None:
    outside = tmp_path / "o"
    outside.mkdir()
    steps = probe.steps_for("f2", tmp_path, outside, "https://example.com")
    made = probe.Leg("f2", tmp_path, steps)
    # A reworded write that reports a denial is not the step.
    made.calls.append(probe.Call("Bash", "1", steps[0].command + " ", True, "PostToolUse", "rc=1"))
    made.calls.append(probe.Call("Bash", "2", steps[2].command, True, "PostToolUse", "rc=1"))
    probe.read_outcomes(made)
    assert made.outcomes == {
        probe.WRITE_SUB: Outcome.NOT_ATTEMPTED,
        probe.FETCH: Outcome.NOT_ATTEMPTED,
        probe.WRITE_LITERAL: Outcome.DENIED,
    }
    steps[0].evidence.write_text("")
    probe.read_outcomes(made)
    assert made.outcomes[probe.WRITE_SUB] is Outcome.LANDED


# ------------------------------------------------------------------------- identity


def test_identity_needs_both_readings() -> None:
    a, b = "id-a", "id-b"
    identity = probe.classify_identity
    assert identity(a, a, a, fs(10), fs(20), []).label == "SAME ID"
    assert identity(a, a, b, fs(10), fs(10), [b]).label == "DIFFERENT ID"
    assert identity(a, a, a, fs(10), fs(20), [b]).label == "DISAGREEMENT"
    assert identity(a, a, b, fs(10), fs(20), []).label == "DISAGREEMENT"
    assert identity(a, a, a, fs(10), fs(10), []).label == "UNREADABLE"
    assert identity(a, a, a, None, None, []).label == "UNREADABLE"
    assert identity(a, a, None, fs(10), fs(20), []).label == "UNREADABLE"
    assert identity(a, None, a, fs(10), fs(20), []).label == "UNREADABLE"
    assert identity(a, b, b, fs(10), fs(20), []).label == "UNREADABLE"


# ---------------------------------------------------------------- P1 and the reverse


def test_history_is_read_from_the_nonce_and_only_when_it_could_have_arrived() -> None:
    hv, decoy, prompt = "HV-1", "DC-1", "no nonce here"
    history = probe.history_verdict
    assert history(hv, True, leg("f2", "n", "history=HV-1"), decoy, prompt).label == "IN CONTEXT"
    assert (
        history(hv, True, leg("f2", "n", "history=NONE"), decoy, prompt).label == "NOT IN CONTEXT"
    )
    assert history(hv, False, leg("f2", "n", "history=NONE"), decoy, prompt).label == "UNREADABLE"
    assert history(hv, True, leg("f2", "n", "", result_ids=()), decoy, prompt).label == "UNREADABLE"
    assert history(hv, True, leg("f2", "n", "HV-1 DC-1"), decoy, prompt).label == "VOID"
    assert history(hv, True, leg("f2", "n", "HV-1"), decoy, "recall HV-1").label == "VOID"


def test_the_system_prompt_reading_is_void_when_history_could_carry_the_old_canary() -> None:
    old, new, decoy = "LC-old", "LC-new", "DC-1"
    f1 = leg("f1", old, "done")
    answered = probe.system_prompt_verdict
    assert answered(f1, leg("f2", new, "launch=LC-new"), False, decoy).label == "THE NEW LAUNCH'S"
    assert answered(f1, leg("f2", new, "launch=LC-old"), False, decoy).label == "THE OLD LAUNCH'S"
    assert answered(f1, leg("f2", new, "launch=LC-old,LC-new"), False, decoy).label == "BOTH"
    assert answered(f1, leg("f2", new, "launch=NONE"), False, decoy).label == "UNREADABLE"
    assert answered(f1, leg("f2", new, "launch=LC-old"), True, decoy).label == "VOID"
    repeated = leg("f1", old, "LC-old")
    assert answered(repeated, leg("f2", new, "launch=LC-old,LC-new"), False, decoy).label == "VOID"
    assert answered(f1, leg("f2", new, "launch=LC-new DC-1"), False, decoy).label == "VOID"
    # The old canary on disk voids nothing when the reply never carries it.
    assert answered(f1, leg("f2", new, "launch=LC-new"), True, decoy).label == "THE NEW LAUNCH'S"


def test_release_is_sticky_on_a_denial_the_host_does_not_share() -> None:
    release = probe.release_verdict
    unread = probe.Verdict("UNREADABLE")
    assert release(SAME, ALL_LANDED, ALL_DENIED, ALL_LANDED).label == "RELEASED"
    assert release(SAME, ALL_LANDED, ALL_DENIED, ALL_DENIED).label == "STICKY"
    one_denied = ALL_LANDED | {probe.FETCH: Outcome.DENIED}
    assert release(SAME, ALL_LANDED, ALL_DENIED, one_denied).label == "STICKY"
    assert release(SAME, {}, ALL_DENIED, ALL_DENIED).label == "NOT MEASURED"
    assert release(SAME, ALL_LANDED, ALL_LANDED, ALL_LANDED).label == "NOT MEASURED"
    assert release(unread, ALL_LANDED, ALL_DENIED, ALL_LANDED).label == "NOT MEASURED"


def test_the_resumed_prompt_carries_the_commands_and_no_value() -> None:
    steps = probe.steps_for("f2", Path("/c"), Path("/o"), "https://example.com")
    text = probe.resumed_prompt(steps)
    assert all(step.command in text for step in steps)
    assert "HV-" not in text and "LC-" not in text and "DC-" not in text
    assert "HV-abc" in probe.fresh_prompt(steps, "HV-abc")
    assert "LC-abc" in probe.system_append("LC-abc")


# -- what the model said, once -------------------------------------------------------


def _spoke(text: str, result: str | None) -> Any:
    leg = probe.Leg("f2", Path("/nowhere"), [])
    leg.text, leg.result_text = text, result
    return leg


def test_a_reply_the_result_message_repeats_is_read_once() -> None:
    """
    ``ResultMessage.result`` is the last assistant text again. Joined blindly, every
    reply printed twice; the canary readings are substring tests and did not care,
    which is how it went unnoticed.
    """
    assert _spoke("done", "done").said == "done"
    assert (
        _spoke("history=HV-1\nlaunch=LC-1", "history=HV-1\nlaunch=LC-1\n").said.count("HV-1") == 1
    )
    assert _spoke("done", None).said == "done"


def test_a_result_that_says_something_the_assistant_text_did_not_is_still_read() -> None:
    """
    The other half: the result is dropped only as a repeat. An error result carries
    text no assistant block held, and a canary reading must still be able to see it.
    """
    said = _spoke("", "Not logged in").said
    assert "Not logged in" in said
    assert "LC-9" in _spoke("launch=", "launch=LC-9").said


# -- scripts/verify_resume_append.py: the verdict on a resumed launch's append -------
#
# Here rather than in a file of its own because the two scripts answer one question
# between them and this is the file their task was given.


def _load_append() -> ModuleType:
    source = Path(__file__).resolve().parent.parent / "scripts" / "verify_resume_append.py"
    spec = importlib.util.spec_from_file_location("pptmstr_test_resume_append", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


append = _load_append()
OLD, NEW, DECOY = "PM-00000000000001", "PM-00000000000002", "PM-0000000000000d"


def _pair(
    fresh_reply: str = f"markers={OLD}",
    resumed_reply: str = f"markers={OLD}",
    *,
    resumed_ids: tuple[str, ...] = (SID,),
    fresh_argv: bool | None = True,
    resumed_argv: bool | None = True,
) -> tuple[Any, Any]:
    """
    A fresh launch whose control landed and the launch that resumed it, as the
    probe would hold them after a run in which the new append was not reported.
    """
    fresh = append.Leg("a-1", Path("/nowhere"), OLD, session_id=SID)
    fresh.launched, fresh.reply, fresh.result_ids = True, fresh_reply, [SID]
    fresh.marker_on_argv = fresh_argv
    resumed = append.Leg("a-2", Path("/nowhere"), NEW, resumes=SID)
    resumed.launched, resumed.reply, resumed.result_ids = True, resumed_reply, list(resumed_ids)
    resumed.marker_on_argv = resumed_argv
    return fresh, resumed


def _label(fresh: Any, resumed: Any) -> str:
    label: str = append.append_verdict(fresh, resumed, DECOY).label
    return label


def test_the_resuming_launchs_marker_in_the_resumed_reply_is_received() -> None:
    """
    That marker is in the resuming launch's append and nowhere else, so its presence
    is the whole finding -- with or without the first launch's beside it.
    """
    assert _label(*_pair(resumed_reply=f"markers={NEW}")) == append.RECEIVED
    assert _label(*_pair(resumed_reply=f"markers={OLD}, {NEW}")) == append.RECEIVED


def test_not_received_needs_the_control_the_resume_and_the_id() -> None:
    """
    The property the probe exists for. The resumed reply lacks the new marker in
    every row here; only the first is allowed to say so.
    """
    assert _label(*_pair()) == append.NOT_RECEIVED
    assert _label(*_pair(resumed_reply="markers=NONE")) == append.NOT_RECEIVED

    # The control did not land: the fresh launch never showed it answers from its
    # system prompt.
    assert _label(*_pair(fresh_reply="markers=NONE")) == append.VOID
    assert _label(*_pair(fresh_reply="I can't share my system prompt.")) == append.VOID

    # The fresh launch did not complete.
    fresh, resumed = _pair()
    fresh.is_error = True
    assert _label(fresh, resumed) == append.VOID
    fresh, resumed = _pair()
    fresh.result_ids = []
    assert _label(fresh, resumed) == append.VOID

    # The resumed launch did not complete, three ways.
    for spoil in ("failure", "is_error", "result_ids"):
        fresh, resumed = _pair()
        setattr(resumed, spoil, {"failure": "boom", "is_error": True, "result_ids": []}[spoil])
        assert _label(fresh, resumed) == append.VOID, spoil

    # Not the same session: a new one receives its append by construction.
    other = "99999999-2222-3333-4444-555555555555"
    assert _label(*_pair(resumed_ids=(other,))) == append.VOID
    assert _label(*_pair(resumed_ids=(SID, other))) == append.VOID


def test_an_invented_marker_voids_the_reading_in_either_reply() -> None:
    """
    The decoy was passed to nothing. A model that produces it is making values up,
    and then a RECEIVED is worth as little as a NOT RECEIVED.
    """
    assert _label(*_pair(resumed_reply=f"markers={NEW}, {DECOY}")) == append.VOID
    assert _label(*_pair(fresh_reply=f"markers={OLD}, {DECOY}")) == append.VOID
    # And the resuming marker has no business in the fresh reply at all.
    assert _label(*_pair(fresh_reply=f"markers={OLD}, {NEW}")) == append.VOID


def test_not_sent_is_said_only_when_the_argv_reader_was_shown_to_work() -> None:
    """
    "The marker was not on the process's argv" is a finding about this side of the
    process boundary, and only if the same reader found the fresh launch's marker on
    the fresh process's. A reader that sees nothing anywhere has shown nothing.
    """
    assert _label(*_pair(resumed_argv=False)) == append.NOT_SENT
    assert _label(*_pair(resumed_argv=False, fresh_argv=None)) == append.NOT_RECEIVED
    assert _label(*_pair(resumed_argv=False, fresh_argv=False)) == append.NOT_RECEIVED
    assert _label(*_pair(resumed_argv=None)) == append.NOT_RECEIVED
    # A marker that did reach the reply was sent, whatever the reader made of argv.
    assert _label(*_pair(resumed_reply=f"markers={NEW}", resumed_argv=False)) == append.RECEIVED


def test_neither_prompt_nor_the_other_append_carries_a_launchs_marker() -> None:
    """
    The discriminating marker has one route to the model. The prompts are fixed
    text, and an append holds the marker it was built for and no other.
    """
    for prompt in (append.fresh_prompt(), append.resumed_prompt()):
        assert not append.MARKER.search(prompt)
    assert append.MARKER.findall(append.append_text(NEW)) == [NEW]
    assert append.MARKER.fullmatch(append.mint())


def test_the_method_is_shown_only_by_an_arm_that_read_received() -> None:
    """
    NOT RECEIVED in every arm is also what a question that can never see an append
    would produce, and the summary has to say which of the two a run was.
    """
    seen = {"forward": append.Verdict(append.NOT_RECEIVED), "both": append.Verdict(append.RECEIVED)}
    assert append.method_verdict(seen).label == "SHOWN"
    blind = {"forward": append.Verdict(append.NOT_RECEIVED), "both": append.Verdict(append.VOID)}
    assert append.method_verdict(blind).label == "NOT SHOWN"
    assert append.method_verdict({}).label == "NOT SHOWN"
