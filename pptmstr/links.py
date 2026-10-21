"""
Open a link's destination in the system's handler for its scheme, behind an allow-list.

The destination is model-written, so this module is a gate before it is an opener.
`followable` is the policy: pure, total, and the only thing deciding what may reach
an OS handler. `follow` is the shell around it: it asks the policy, builds the argv,
and hands it to an injected launcher, turning every failure into a one-line reason
rather than an exception, because it is called from the frame loop.

Policy, as it holds now:

- Only `http`, `https` and `mailto` are followed (`ALLOWED_SCHEMES`). `xdg-open` and
  `open` dispatch on scheme to whatever is registered, and for `file:` or an
  application scheme (`vscode://`, `ms-*`, `slack://`, ...) that can mean running an
  executable, a `.desktop` file or an application action. These three are what a
  reader expects a click on a link to open. Adding a scheme is a one-line change to
  that set and is meant to be made deliberately, with its handler in mind.
- The scheme is compared case-insensitively (`HTTPS:` is `https:`; `JaVaScRiPt:` is
  still refused). A destination with no scheme -- a bare path, a fragment, a
  scheme-relative `//host` -- is refused rather than resolved against anything.
- Whitespace and control characters anywhere are refused, as are the invisible
  format characters (bidi overrides, zero-width joiners) and lone surrogates. The
  destination is shown to the reader inline, and these are the characters that make
  what is shown differ from what is opened, or that cannot be passed in an argv.
- A destination longer than `MAX_LENGTH` (8 KiB) is refused. The reader is meant to
  be able to read where a click goes, and nothing that long is read; it also sits far
  under every platform's single-argument limit (128 KiB on Linux), so the refusal is
  ours and never an opaque E2BIG from the kernel.
- `http(s)` needs a host, and may not name a user before it (`https://a@b/`).
  Userinfo has no use in a link a model writes, and it is how a destination is made
  to begin with a name the reader trusts: `https://docs.python.org@evil.example/`
  goes to `evil.example`.
- A `mailto:` query may carry only RFC 6068's `to`, `cc`, `bcc`, `subject` and
  `body` (`MAILTO_FIELDS`), each at most once, case-insensitively. Any other field
  is refused: `attach=` and `attachment=` are honoured by some mail clients as a
  local file to attach (CVE-2020-11879, CVE-2020-11880), and the address is model
  output. The names are checked as written and again percent-decoded, so an
  encoded `?`, `&` or `=` that a client decoding first would read as another field
  is refused too.
- A destination can never begin with `-`, because a scheme begins with a letter,
  so it cannot be read as an option by the opener; `_spawn` checks that again at
  the point the argv is built.
- The opener is handed the destination with its scheme lower-cased (RFC 3986
  section 3.1: a scheme's case is not significant). xdg-open's generic mode looks
  its handler up as `x-scheme-handler/<scheme>` as written.

Launching uses `xdg-open` on Linux and `open` on macOS through `subprocess.Popen`
with an argv list, no shell, the standard streams on DEVNULL and a new session, and
no wait on the frame thread. A daemon thread waits on the child so it is reaped and
does not linger as a zombie for the life of the app.

**The DEVNULL streams and the new session are what keep a handler off pptmstr's
terminal**, and not the choice of opener. xdg-open's generic mode (read in
xdg-open 1.1.3, 2026-10-20) also honours `$BROWSER`, and falls back to console
browsers (`www-browser`, `links2`, `elinks`, `links`, `lynx`, `w3m`); a desktop
entry with `Terminal=true` is run in a terminal too. A child with no controlling
terminal and no inherited streams cannot draw on the one pptmstr runs in: it
fails, or goes to `x-terminal-emulator`, a window of its own.

`webbrowser` is not used, for http(s) either. On its first call it runs
`xdg-settings` synchronously, which would stall the frame; with `TERM` set it
registers console browsers (lynx, w3m) and runs them on the terminal pptmstr
inherited, and waits for them, on the calling thread; on macOS it builds an
AppleScript around the URL; and it does not handle `mailto` consistently. One
opener for all three schemes keeps the behaviour the same whichever is clicked.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import unicodedata
from collections.abc import Callable, Sequence
from urllib.parse import unquote, urlsplit

from .log import LOG

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})

MAX_LENGTH = 8 * 1024

MAILTO_FIELDS: frozenset[str] = frozenset({"to", "cc", "bcc", "subject", "body"})

# RFC 3986 section 3.1: a scheme is a letter followed by letters, digits, + - .
_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*):")

# Cc control (which holds the ASCII whitespace and U+0085), Cf format (bidi
# overrides, zero-width), Cs lone surrogate, and the three separator categories.
# Every character str.isspace accepts falls in one of these.
_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zs", "Zl", "Zp"})

_OPENERS: dict[str, str] = {"linux": "xdg-open", "darwin": "open"}

# Long enough to recognise the destination in the log, short enough to keep the
# log readable when the refusal was for length.
_LOGGED_LENGTH = 200

Launch = Callable[[Sequence[str]], None]


def followable(destination: str) -> str | None:
    """
    None if `destination` may be handed to the system's opener, else why it may not.
    """
    if not destination:
        return "the destination is empty"
    if len(destination) > MAX_LENGTH:
        return f"the destination is longer than {MAX_LENGTH} characters"
    for ch in destination:
        if unicodedata.category(ch) in _REFUSED_CATEGORIES:
            return f"the destination contains U+{ord(ch):04X}, which is not followed"
    match = _SCHEME.match(destination)
    if match is None:
        return "the destination has no scheme"
    scheme = match.group(1).lower()
    if scheme not in ALLOWED_SCHEMES:
        return f"the {scheme}: scheme is not followed"
    rest = destination[match.end() :]
    if not rest:
        return f"the {scheme}: destination is empty after the scheme"
    if scheme in ("http", "https"):
        try:
            host = urlsplit(destination).hostname
        except ValueError:
            return f"the {scheme}: destination does not parse as a URL"
        if not host:
            return f"the {scheme}: destination names no host"
        if urlsplit(destination).username is not None:
            return f"the {scheme}: destination names a user before its host"
    if scheme == "mailto":
        return _mailto_refusal(rest)
    return None


def _mailto_refusal(rest: str) -> str | None:
    """
    Why a `mailto:` destination's text after the scheme is refused, or None.

    Read twice: as written, and percent-decoded, which is what a client that decodes
    before it splits would read. A field that has no `=` in the decoded reading is a
    piece of a value (an encoded `&` in a subject) and is not a name.
    """
    address, _, query = rest.partition("?")
    if "?" in unquote(address):
        return "the mailto: address holds an encoded '?'"
    for decoded in (False, True):
        seen: set[str] = set()
        for field in (unquote(query) if decoded else query).split("&"):
            if not field or (decoded and "=" not in field):
                continue
            name = field.partition("=")[0].lower()
            if name not in MAILTO_FIELDS:
                return f"the mailto: field {name[:40]!r} is not followed"
            if name in seen:
                return f"the mailto: field {name!r} is given twice"
            seen.add(name)
    return None


def opener_argv(destination: str, platform: str) -> list[str] | None:
    """
    The argv that opens `destination` on `platform`, or None if no opener is known.
    """
    tool = _OPENERS.get(platform)
    if tool is None:
        return None
    match = _SCHEME.match(destination)
    if match is not None:
        destination = match.group(1).lower() + destination[match.end() - 1 :]
    return [tool, destination]


def _spawn(argv: Sequence[str]) -> None:
    if len(argv) != 2 or argv[1].startswith("-"):
        raise ValueError("the destination could be read as an option")
    child = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    threading.Thread(target=child.wait, name="link-reaper", daemon=True).start()


def _shown(destination: str) -> str:
    if len(destination) <= _LOGGED_LENGTH:
        return repr(destination)
    return repr(destination[:_LOGGED_LENGTH]) + f" (+{len(destination) - _LOGGED_LENGTH})"


def follow(
    destination: str,
    *,
    launch: Launch = _spawn,
    platform: str = sys.platform,
) -> str | None:
    """
    Open `destination` in the system's handler. None on success, else a one-line reason.

    Never raises: a refusal, a platform with no opener, a missing opener binary or
    any error from the launcher comes back as the reason, and is logged.
    """
    argv: list[str] | None = None
    try:
        refusal = followable(destination)
        if refusal is not None:
            LOG.warn("link", f"refused {_shown(destination)}: {refusal}")
            return refusal
        argv = opener_argv(destination, platform)
        if argv is None:
            reason = f"no link opener is known on {platform}"
            LOG.warn("link", f"{_shown(destination)}: {reason}")
            return reason
        launch(argv)
    except FileNotFoundError:
        reason = f"{argv[0]} was not found" if argv else "the link opener was not found"
        LOG.error("link", f"{_shown(destination)}: {reason}")
        return reason
    except Exception as exc:  # noqa: BLE001 -- the frame loop must not see it
        reason = f"the link could not be opened: {type(exc).__name__}: {exc}"
        reason = " ".join(reason.split())
        LOG.error("link", f"{_shown(destination)}: {reason}")
        return reason
    LOG.info("link", f"opened {_shown(destination)}")
    return None
