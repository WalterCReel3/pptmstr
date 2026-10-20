"""
The link opener: what the policy lets through, and what follow() hands the launcher.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Sequence

import pytest

from pptmstr import links
from pptmstr.links import MAX_LENGTH, follow, followable

ALLOWED = [
    "https://example.com",
    "http://example.com/a?b=c#d",
    "HTTPS://EXAMPLE.COM/",
    "HtTp://example.com",
    "mailto:someone@example.com",
    "mailto:someone@example.com?subject=Hi%20there&body=x",
    "MAILTO:someone@example.com",
    "https://xn--bcher-kva.example/",
    "https://bücher.example/",
    "https://user@example.com:8443/path",
    "https://" + "a" * (MAX_LENGTH - len("https://")),
]

REFUSED = [
    "",
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "file:///etc/passwd",
    "FILE:///etc/passwd",
    "data:text/html,<script>alert(1)</script>",
    "vscode://file/etc/passwd",
    "ms-settings:display",
    "slack://open",
    "//example.com/path",
    "/etc/passwd",
    "example.com",
    "#anchor",
    "-help",
    "--version",
    "https:",
    "https://",
    "https:///path",
    "mailto:",
    " https://example.com",
    "https://example.com ",
    "https://exa mple.com",
    "https://example.com/\tx",
    "https://example.com/\nx",
    "https://example.com/\x00",
    "https://example.com/\x7f",
    "https://example.com/\u202e",
    "https://example.com/\u200b",
    "https://example.com/\u00a0",
    "https://example.com/\u2028",
    "https://example.com/\u2029",
    "https://example.com/\u0085",
    "https://example.com/\udc80",
    "https://" + "a" * (MAX_LENGTH - len("https://") + 1),
    "http://[::1",
]


@pytest.mark.parametrize("destination", ALLOWED)
def test_an_allowed_destination_is_followable(destination: str) -> None:
    assert followable(destination) is None


@pytest.mark.parametrize("destination", REFUSED)
def test_a_refused_destination_says_why(destination: str) -> None:
    reason = followable(destination)
    assert reason is not None
    assert "\n" not in reason


def test_the_refusal_names_the_scheme_it_refused() -> None:
    assert followable("JaVaScRiPt:alert(1)") == "the javascript: scheme is not followed"


class Recorder:
    def __init__(self, raises: BaseException | None = None) -> None:
        self.calls: list[list[str]] = []
        self.raises = raises

    def __call__(self, argv: Sequence[str]) -> None:
        self.calls.append(list(argv))
        if self.raises is not None:
            raise self.raises


@pytest.mark.parametrize(("platform", "tool"), [("linux", "xdg-open"), ("darwin", "open")])
def test_follow_hands_the_launcher_exactly_the_opener_and_the_destination(
    platform: str, tool: str
) -> None:
    launch = Recorder()
    url = "mailto:a@example.com?subject=x&body=y"
    assert follow(url, launch=launch, platform=platform) is None
    assert launch.calls == [[tool, url]]


@pytest.mark.parametrize("destination", REFUSED)
def test_a_refused_destination_never_reaches_the_launcher(destination: str) -> None:
    launch = Recorder()
    assert follow(destination, launch=launch, platform="linux") is not None
    assert launch.calls == []


def test_a_platform_with_no_opener_is_a_reason_not_a_launch() -> None:
    launch = Recorder()
    reason = follow("https://example.com", launch=launch, platform="win32")
    assert reason == "no link opener is known on win32"
    assert launch.calls == []


def test_a_missing_opener_binary_is_a_reason() -> None:
    launch = Recorder(FileNotFoundError(2, "No such file or directory", "xdg-open"))
    assert follow("https://example.com", launch=launch, platform="linux") == (
        "xdg-open was not found"
    )


@pytest.mark.parametrize(
    "exc", [OSError(13, "Permission denied"), RuntimeError("line one\nline two")]
)
def test_a_raising_launcher_becomes_a_one_line_reason(exc: Exception) -> None:
    reason = follow("https://example.com", launch=Recorder(exc), platform="linux")
    assert reason is not None
    assert reason.startswith("the link could not be opened: ")
    assert "\n" not in reason


def test_the_real_spawner_refuses_an_argv_that_reads_as_an_option() -> None:
    with pytest.raises(ValueError):
        links._spawn(["xdg-open", "--help"])


def test_the_real_spawner_uses_an_argv_list_no_shell_and_reaps_the_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    waited: list[bool] = []

    class FakeChild:
        def wait(self) -> int:
            waited.append(True)
            return 0

    def fake_popen(args: object, **kwargs: object) -> FakeChild:
        seen["args"] = args
        seen.update(kwargs)
        return FakeChild()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    started: list[threading.Thread] = []
    real_start = threading.Thread.start

    def tracking_start(self: threading.Thread) -> None:
        started.append(self)
        real_start(self)

    monkeypatch.setattr(threading.Thread, "start", tracking_start)
    links._spawn(["xdg-open", "https://example.com"])
    for thread in started:
        thread.join(timeout=5)

    assert seen["args"] == ["xdg-open", "https://example.com"]
    assert seen.get("shell", False) is False
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["stdout"] is subprocess.DEVNULL
    assert seen["stderr"] is subprocess.DEVNULL
    assert seen["start_new_session"] is True
    assert [t.daemon for t in started] == [True]
    assert waited == [True]
