"""
Claim 5: boardlog._file_bytes and channels._cannot_be_read follow a symlink placed
under a record name; commandfile._file_bytes, sessionfile._file_bytes and
requests._contents refuse one.

In a temp dir: a real record is appended (boardlog.append) to an "elsewhere"
directory, and a symlink to it is placed under ordinal 0's name in a "log"
directory. Each reader is called on the symlink directly. Then boardlog.read on the
log directory, which is the path both board logs and channel logs are read through
(channels.ChannelLog.read calls boardlog.read).
"""

from __future__ import annotations

import errno
import os
import time

import _common
from _common import verdict

from pptmstr import boardlog, channels, commandfile, requests, sessionfile

base = _common.SANDBOX / "p05"
elsewhere, log = base / "elsewhere", base / "log"
elsewhere.mkdir(parents=True)
log.mkdir()

marker = boardlog.RecordingStopped(at=time.time(), reason="planted outside the log")
print("append to elsewhere:", boardlog.append(elsewhere, marker, durable=False))
target = elsewhere / boardlog._name(0)
link = log / boardlog._name(0)
os.symlink(target, link)
dangling = log / "dangling.json"
os.symlink(base / "nothing-here", dangling)
print(f"symlink {link.name} -> {os.readlink(link)}  (is_symlink={link.is_symlink()})")
print()


def attempt(label: str, fn) -> str:  # type: ignore[no-untyped-def]
    try:
        got = fn()
    except OSError as exc:
        name = errno.errorcode.get(exc.errno or 0, "?")
        out = f"raised OSError {name}: {exc.strerror or exc}"
    else:
        shown = got if isinstance(got, (bool, str)) else f"{len(got)} bytes, starts {bytes(got)[:40]!r}"
        out = f"returned {shown}"
    print(f"  {label:<44} {out}")
    return out


print("== each reader, called on the symlink under the record name")
r_board = attempt("boardlog._file_bytes(link)", lambda: boardlog._file_bytes(link))
r_chan = attempt("channels._cannot_be_read(link)", lambda: channels._cannot_be_read(link))
r_chan_d = attempt("channels._cannot_be_read(dangling link)", lambda: channels._cannot_be_read(dangling))
r_cf = attempt("commandfile._file_bytes(link, 1<<20)", lambda: commandfile._file_bytes(link, 1 << 20))
r_sf = attempt("sessionfile._file_bytes(link)", lambda: sessionfile._file_bytes(link))
r_rq = attempt("requests._contents(link)", lambda: requests._contents(link))
print()
print("  (channels._cannot_be_read returns False for 'readable, or not a regular file';")
print("   with O_NOFOLLOW the open would fail ELOOP and it would return True.)")

print("\n== end to end: boardlog.read(log) -- the board-log and channel-log read path")
read = boardlog.read(log)
print(f"  records={read.records!r}")
print(f"  damage={read.damage!r}")
followed_end_to_end = len(read.records) == 1 and read.damage is None

board_follows = r_board.startswith("returned")
chan_follows = r_chan == "returned False"
siblings_refuse = all("ELOOP" in r for r in (r_cf, r_sf, r_rq))
print()
print(f"boardlog follows: {board_follows}; channels._cannot_be_read follows: {chan_follows}; "
      f"siblings refuse with ELOOP: {siblings_refuse}; boardlog.read restores the planted record: "
      f"{followed_end_to_end}")
if board_follows and chan_follows and siblings_refuse:
    verdict(
        "CONFIRMED",
        "boardlog._file_bytes reads through the link and boardlog.read returns the record "
        "it points at as ordinal 0; channels._cannot_be_read opens it as readable; "
        "commandfile/sessionfile/requests raise ELOOP (O_NOFOLLOW)",
    )
else:
    verdict("REFUTED", "see the per-reader lines above")
