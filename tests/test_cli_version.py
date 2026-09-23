"""
The CLI version floor: parsing, comparison, and the totality of the read.

The pure half is exercised by handing ``check_floor`` a ``VersionRead`` directly. The
blocking half is exercised against real executables written into ``tmp_path`` rather
than a mock, because the failures being asserted -- a path that does not exist, a
binary that is not executable, a non-zero exit -- are the operating system's behaviour
and a mock would only assert this file's idea of it.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from pptmstr.cli_version import (
    SANDBOX_FLOOR,
    SANDBOX_FLOOR_SOURCES,
    BelowFloor,
    MeetsFloor,
    Unreadable,
    VersionRead,
    check_floor,
    check_installed_cli,
    parse_version,
    read_cli_version,
    resolve_cli_path,
)


def _reading(text: str) -> VersionRead:
    return VersionRead(text, f"a fake CLI reported {text!r}")


def _fake_cli(tmp_path: Path, name: str, body: str) -> str:
    """
    Write an executable script that stands in for the CLI, and return its path.
    """
    script = tmp_path / name
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


# ---------------------------------------------------------------------------
# The floor itself
# ---------------------------------------------------------------------------


def test_the_floor_is_the_highest_of_the_keys_the_configuration_uses():
    """
    The constant is a literal so it is greppable; this is what stops it drifting.

    planning/2026-09-03 §8 documents floors for three settings keys and the §8
    configuration uses two of them. Recomputing from the recorded sources here means a
    later key added to SANDBOX_FLOOR_SOURCES with a higher floor cannot be added
    without the constant moving with it.
    """
    assert SANDBOX_FLOOR == max(SANDBOX_FLOOR_SOURCES.values(), key=parse_version)
    assert SANDBOX_FLOOR == "2.1.219"


def test_the_floor_sources_omit_the_key_the_configuration_does_not_set():
    """
    §8's JSON sets no ``filesystem`` key, so its documented 2.1.216 floor is not ours.

    A floor for a key nobody sets would refuse launches on versions that can run this
    configuration perfectly well.
    """
    assert "filesystem.disabled" not in SANDBOX_FLOOR_SOURCES
    assert set(SANDBOX_FLOOR_SOURCES) == {"credentials", "network.strictAllowlist"}


# ---------------------------------------------------------------------------
# Parsing and comparison
# ---------------------------------------------------------------------------


def test_the_version_the_cli_actually_prints_parses():
    """
    ``claude --version`` prints ``2.1.251 (Claude Code)``: the product name trails it.
    """
    assert parse_version("2.1.251 (Claude Code)") == (2, 1, 251)


@pytest.mark.parametrize("text", ["", "unknown", "claude code", "v2.1.219", " (Claude Code)"])
def test_a_string_with_no_leading_version_is_unparseable(text: str):
    """
    Anchored at the start, so a number later in the line is not mistaken for a version.
    """
    assert parse_version(text) is None


def test_a_version_above_the_floor_meets_it():
    verdict = check_floor(_reading("2.1.251 (Claude Code)"))
    assert isinstance(verdict, MeetsFloor)
    assert verdict.components == (2, 1, 251)
    assert verdict.floor == SANDBOX_FLOOR


def test_a_version_equal_to_the_floor_meets_it():
    """
    The floor is inclusive: 2.1.219 is the version ``network.strictAllowlist`` landed
    in, so it is the oldest CLI that can honour the configuration, not the oldest that
    cannot.
    """
    verdict = check_floor(_reading("2.1.219 (Claude Code)"))
    assert isinstance(verdict, MeetsFloor)
    assert verdict.components == (2, 1, 219)


def test_a_version_below_the_floor_is_refused_and_says_what_it_was():
    verdict = check_floor(_reading("2.1.200 (Claude Code)"))
    assert isinstance(verdict, BelowFloor)
    assert verdict.version == "2.1.200 (Claude Code)"
    assert verdict.components == (2, 1, 200)
    assert verdict.floor == SANDBOX_FLOOR


def test_a_multi_digit_component_compares_numerically_not_as_text():
    """
    The case a string comparison gets backwards: "2.1.9" > "2.1.187" lexically, and
    2.1.9 is eleven months of releases *older*. Getting this wrong admits a CLI that
    silently ignores every sandbox key.
    """
    assert "2.1.9" > "2.1.187"  # the trap, stated so the test's point is visible
    assert isinstance(check_floor(_reading("2.1.9"), floor="2.1.187"), BelowFloor)
    assert isinstance(check_floor(_reading("2.1.187"), floor="2.1.9"), MeetsFloor)


def test_a_major_version_bump_clears_the_floor_regardless_of_the_later_components():
    """
    Comparison is left-to-right per component, not a sum or a flattened number.
    """
    assert isinstance(check_floor(_reading("3.0.0")), MeetsFloor)
    assert isinstance(check_floor(_reading("2.2.0")), MeetsFloor)
    assert isinstance(check_floor(_reading("2.0.999")), BelowFloor)


def test_components_of_unequal_length_pad_rather_than_compare_short():
    """
    A two-component version is a real possibility from a CLI this code does not
    control, and 2.1 must read as 2.1.0 rather than as something below it.

    Python's own tuple comparison treats a prefix as *smaller* -- ``(2, 1) < (2, 1, 0)``
    is True -- so without the zero-fill a CLI reporting "2.1" would be refused against a
    floor of "2.1.0" that it exactly meets. That pair is the one assertion here that
    distinguishes padding from raw tuple comparison.
    """
    assert isinstance(check_floor(_reading("2.1"), floor="2.1.0"), MeetsFloor)
    assert isinstance(check_floor(_reading("2.1"), floor="2.1.219"), BelowFloor)
    assert isinstance(check_floor(_reading("2.2"), floor="2.1.219"), MeetsFloor)
    assert isinstance(check_floor(_reading("2.1.0"), floor="2.1"), MeetsFloor)


def test_an_unparseable_version_is_unreadable_and_not_below_the_floor():
    """
    The distinction §8 demands: a version that could not be read is not the same answer
    as a version known to be too old, and the SDK's ``except Exception: pass`` is what
    happens when they collapse together.
    """
    verdict = check_floor(_reading("Claude Code"))
    assert isinstance(verdict, Unreadable)
    assert not isinstance(verdict, BelowFloor)
    assert "not a version" in verdict.detail


def test_an_unparseable_floor_refuses_rather_than_raising():
    """
    A bad pinned constant must not take a launch down with a traceback.
    """
    verdict = check_floor(_reading("2.1.251"), floor="latest")
    assert isinstance(verdict, Unreadable)
    assert "latest" in verdict.detail


def test_the_reason_a_read_failed_survives_into_the_verdict():
    """
    ``Unreadable.detail`` is what an operator-facing refusal quotes, so a reading that
    failed for an interesting reason must not be flattened into a generic message.
    """
    verdict = check_floor(VersionRead(None, "the claude CLI is not on PATH"))
    assert isinstance(verdict, Unreadable)
    assert verdict.detail == "the claude CLI is not on PATH"


# ---------------------------------------------------------------------------
# The blocking read
# ---------------------------------------------------------------------------


def test_a_missing_binary_is_unreadable_rather_than_an_exception(tmp_path: Path):
    """
    §8's requirement is that the launch path gets an answer it can refuse on. A raised
    FileNotFoundError in the UI thread is not that.
    """
    absent = str(tmp_path / "definitely-not-here")
    read = read_cli_version(absent)
    assert read.text is None
    assert absent in read.detail
    assert isinstance(check_installed_cli(absent), Unreadable)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits and /bin/sh")
def test_a_binary_that_cannot_be_executed_is_unreadable(tmp_path: Path):
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\necho 2.1.251\n", encoding="utf-8")
    script.chmod(0o600)
    read = read_cli_version(str(script))
    assert read.text is None
    assert "PermissionError" in read.detail


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits and /bin/sh")
def test_a_nonzero_exit_is_unreadable_and_carries_the_first_line_of_output(tmp_path: Path):
    """
    A CLI that fails to start still prints something; quoting it is the difference
    between a refusal an operator can act on and one they cannot.
    """
    cli = _fake_cli(tmp_path, "claude", "echo 'cannot find module' >&2\nexit 3")
    read = read_cli_version(cli)
    assert read.text is None
    assert "exited 3" in read.detail
    assert "cannot find module" in read.detail


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits and /bin/sh")
def test_a_clean_run_reads_the_version_and_clears_the_floor(tmp_path: Path):
    cli = _fake_cli(tmp_path, "claude", "echo '2.1.251 (Claude Code)'")
    verdict = check_installed_cli(cli)
    assert isinstance(verdict, MeetsFloor)
    assert verdict.version == "2.1.251 (Claude Code)"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits and /bin/sh")
def test_an_old_cli_is_refused_end_to_end(tmp_path: Path):
    """
    The whole point of the module, through the real subprocess path: a CLI that would
    silently ignore ``network.strictAllowlist`` does not get to start the mode.
    """
    cli = _fake_cli(tmp_path, "claude", "echo '2.1.187 (Claude Code)'")
    verdict = check_installed_cli(cli)
    assert isinstance(verdict, BelowFloor)
    assert verdict.components == (2, 1, 187)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits and /bin/sh")
def test_output_after_the_version_line_does_not_reach_the_parser(tmp_path: Path):
    """
    An update notice printed under the version would otherwise be part of the string
    the verdict reports back.
    """
    cli = _fake_cli(tmp_path, "claude", "echo '2.1.251 (Claude Code)'\necho 'update available'")
    read = read_cli_version(cli)
    assert read.text == "2.1.251 (Claude Code)"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits and /bin/sh")
def test_a_silent_success_is_unreadable(tmp_path: Path):
    """
    Exit 0 with no output must not read as an empty version that then parses to
    nothing in some later caller.
    """
    cli = _fake_cli(tmp_path, "claude", "exit 0")
    read = read_cli_version(cli)
    assert read.text is None
    assert "printed nothing" in read.detail


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits and /bin/sh")
def test_an_undecodable_byte_is_an_answer_rather_than_an_exception(tmp_path: Path):
    """
    A strict decode of ``0xFF`` raises ``UnicodeDecodeError``, which is a ``ValueError``
    and so is neither ``SubprocessError`` nor ``OSError``: it escapes both handlers and
    ends the launch path in a traceback rather than the refusal §8 requires. The byte is
    invalid as UTF-8 and as ASCII both, so the hazard does not depend on the locale.
    """
    cli = _fake_cli(tmp_path, "claude", r"printf 'not \377 a version\n'")
    verdict = check_installed_cli(cli)
    assert isinstance(verdict, Unreadable)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits and /bin/sh")
def test_a_cli_that_never_answers_is_unreadable_rather_than_a_hang(tmp_path: Path):
    """
    Blocking IO on a launch path needs a bound. Without the timeout this call is the
    one that wedges the UI.
    """
    cli = _fake_cli(tmp_path, "claude", "sleep 30")
    read = read_cli_version(cli, timeout=0.5)
    assert read.text is None
    assert "did not answer" in read.detail


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits and /bin/sh")
def test_the_read_does_not_raise_for_any_of_the_failure_shapes(tmp_path: Path):
    """
    Totality stated as one claim rather than inferred from the cases above passing.
    """
    candidates = [
        str(tmp_path / "absent"),
        _fake_cli(tmp_path, "boom", "exit 1"),
        _fake_cli(tmp_path, "garbage", "echo not-a-version"),
        _fake_cli(tmp_path, "quiet", "exit 0"),
    ]
    for candidate in candidates:
        verdict = check_installed_cli(candidate)
        assert isinstance(verdict, Unreadable), candidate


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_the_bundled_cli_is_preferred_over_whatever_is_on_path():
    """
    The SDK spawns its bundled copy when there is one, so reading a PATH copy's version
    would be reading a binary that never launches. Asserted only when the installed
    wheel actually bundles one -- the wheel for a platform without a bundled binary is
    a legitimate install and this test has nothing to say about it.
    """
    claude_agent_sdk = pytest.importorskip("claude_agent_sdk")
    package = claude_agent_sdk.__file__
    assert package is not None
    name = "claude.exe" if sys.platform == "win32" else "claude"
    bundled = Path(package).parent / "_bundled" / name
    if not bundled.is_file():
        pytest.skip("this wheel bundles no CLI")
    assert resolve_cli_path() == str(bundled)


def test_resolution_answers_none_rather_than_raising_when_nothing_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    A machine with no CLI at all must produce a refusal, not a CLINotFoundError -- the
    SDK's ``_find_cli`` raises, which is why it is not called here.
    """
    monkeypatch.setattr("pptmstr.cli_version.shutil.which", lambda _name: None)
    monkeypatch.setattr("pptmstr.cli_version.Path", _PathMissingBundle)
    assert resolve_cli_path() is None


class _PathMissingBundle(Path):
    """
    A ``Path`` whose bundled-CLI probe always reports absence.

    Subclassed rather than monkeypatched onto ``Path`` itself so nothing outside this
    module's resolution call sees the altered behaviour.
    """

    if sys.version_info < (3, 12):
        _flavour = type(Path())._flavour  # type: ignore[attr-defined]

    def is_file(self) -> bool:
        return False

    def exists(self, *args: object, **kwargs: object) -> bool:
        return False


def test_the_real_installed_cli_reads_and_is_judged():
    """
    One end-to-end read of a real binary, through resolution and subprocess together.

    What it pins is that the read completes and hands back a verdict a refusal can be
    written from: reaching the assertions is the claim that nothing raised, and each arm
    must carry the pinned floor and agree with itself about the version it reports.

    It deliberately does not pin *which* arm. "The CLI on this machine clears the floor"
    would fail the suite on a correctly-refusing old install, which is the behaviour the
    module exists to produce.
    """
    if resolve_cli_path() is None:
        pytest.skip("no claude CLI on this machine")
    verdict = check_installed_cli()
    assert verdict.floor == SANDBOX_FLOOR
    if isinstance(verdict, Unreadable):
        assert verdict.detail.strip()
    else:
        assert parse_version(verdict.version) == verdict.components


def test_a_directory_in_place_of_the_binary_is_unreadable(tmp_path: Path):
    """
    ``os.access`` would call a directory executable; running it is what actually fails,
    and that failure has to land in ``detail`` rather than propagating.
    """
    target = tmp_path / "a-directory"
    target.mkdir()
    assert os.access(target, os.X_OK)
    read = read_cli_version(str(target))
    assert read.text is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits and /bin/sh")
def test_the_fake_cli_helper_really_runs(tmp_path: Path):
    """
    The blocking tests are only evidence if the stand-in is executable at all; a helper
    silently producing an unrunnable file would make every one of them pass for the
    wrong reason.
    """
    cli = _fake_cli(tmp_path, "sanity", "echo hello")
    proc = subprocess.run([cli], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "hello"
