"""
Probes behind the atomic-write design. Each prints what it observed; nothing asserts.

Run from the repository root with the repo venv:

    .venv/bin/python planning/2026-11-07-atomic-writes/probes.py

Every file is written under a fresh directory in ``$TMPDIR``. The modules probed are
imported from the working tree, so run this on a tree at the commit the design names.

- q1: ``brief.write_entry`` finds its next ordinal by *reading* every entry. An entry
  that matches the naming and cannot be read is skipped by ``read_entries``, so the
  ordinal it holds is offered again. With an explicit kind the name differs and the
  link succeeds: two entries share an ordinal.
- q2: an entry that is not UTF-8 makes ``write_entry`` raise before anything is
  written, so a brief with one bad byte cannot be appended to.
- q3: a ``replace`` through ``mkstemp`` leaves the target with mkstemp's 0600, whatever
  mode the file had. Probed through ``settings.save``.
- q4: the text-mode writes equal an encode-then-write of the same text, so a bytes
  primitive changes no byte: ``layout_ini``'s ``newline=""`` on a CRLF file, and
  ``settings``' ``json.dump`` + ``"\\n"``.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from pptmstr import brief, layout_ini, settings


def fresh() -> Path:
    return Path(tempfile.mkdtemp(prefix="atomic-writes-", dir=os.environ.get("TMPDIR")))


def q1() -> None:
    d = fresh()
    brief.write_entry(d, "first")
    blocked = brief.write_entry(d, "second", kind="correction")
    os.chmod(blocked, 0)
    try:
        written = brief.write_entry(d, "third")
        names = sorted(p.name for p in d.iterdir() if not p.name.startswith("."))
        print(f"q1 unreadable {blocked.name}: wrote {written.name}; names now {names}")
        again = brief.write_entry(d, "fourth", kind="correction")
        print(f"q1 explicit kind after it: wrote {again.name}")
    finally:
        os.chmod(blocked, 0o600)


def q2() -> None:
    d = fresh()
    brief.write_entry(d, "first")
    (d / "001-amendment.md").write_bytes(b"\xff\xfe not utf-8\n")
    try:
        written = brief.write_entry(d, "second")
        print(f"q2 wrote {written.name}")
    except Exception as exc:  # noqa: BLE001 -- the shape is what is being observed
        left = sorted(p.name for p in d.iterdir())
        print(f"q2 raised {type(exc).__name__}; directory holds {left}")


def q3() -> None:
    d = fresh()
    target = d / "settings.json"
    target.write_text("{}\n")
    os.chmod(target, 0o644)
    before = stat.S_IMODE(os.stat(target).st_mode)
    settings.save(settings.Settings(), target, unread=None)
    after = stat.S_IMODE(os.stat(target).st_mode)
    print(f"q3 settings.json mode {before:o} -> {after:o}")


def q4() -> None:
    d = fresh()
    ini = d / "imgui.ini"
    marker = f";;;<<<{layout_ini._IMGUI_PART}0_Triage>>>;;;"
    original = (
        f"{marker}\r\n[Window][BOARD]\r\nPos=0,0\r\nDockId=0x00000003,1\r\n\r\n"
    ).encode("utf-8")
    ini.write_bytes(original)
    done = layout_ini.keep_layout_for(ini, "DIGEST", "BOARD")
    on_disk = ini.read_bytes()
    with open(ini, encoding="utf-8", newline="") as handle:
        text = handle.read()
    added = b"DIGEST" in on_disk
    bare_lf = b"\n" in on_disk.replace(b"\r\n", b"")
    print(
        f"q4 layout_ini: {done!r}; entry added: {added}; a bare LF in the file: {bare_lf};"
        f" bytes == text.encode: {on_disk == text.encode('utf-8')}"
    )
    s = d / "s.json"
    settings.save(settings.Settings(), s, unread=None)
    encoded = (
        json.dumps(settings.encoded(settings.Settings()), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    print(f"q4 settings: json.dump + newline == dumps+newline encoded: {s.read_bytes() == encoded}")


if __name__ == "__main__":
    for probe in (q1, q2, q3, q4):
        try:
            probe()
        except Exception as exc:  # noqa: BLE001
            print(f"{probe.__name__} could not run: {type(exc).__name__}: {exc}")
