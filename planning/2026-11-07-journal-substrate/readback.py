"""
B1's condition, measured: logs HEAD writes read back through the proposed journal
path to the same records, the same damage and the same bytes.

Run from the repository root:

    .venv/bin/python planning/2026-11-07-journal-substrate/readback.py
    .venv/bin/python planning/2026-11-07-journal-substrate/readback.py --existing DIR
    .venv/bin/python planning/2026-11-07-journal-substrate/readback.py --write-golden

Every log this writes is under a fresh directory in ``$TMPDIR``, and
``XDG_CONFIG_HOME`` is pointed there before pptmstr is imported, so nothing under
``~/.config`` is read or written. ``--existing DIR`` additionally reads, and never
writes, every log directory under ``DIR`` through both paths; point it at a *copy*
of the operator's configuration directory.

What "the proposed path" is: ``proto_journal.decode`` and ``proto_journal.read``,
which take the kind table as a parameter, with a board log read under the board's
table and a channel log under a table holding only ``RecordingStopped``. Writing is
not re-implemented: ``append`` and ``encode`` move without a change to their
bodies, so the writer here is HEAD's, and part 1 checks that the two functions that
do change differ from HEAD's only as the design says.

Exits non-zero if any check that is expected to hold does not. The one difference
the design predicts (part 6) is reported, not failed.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import inspect
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, get_args, get_type_hints

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

SCRATCH = Path(tempfile.mkdtemp(prefix="b1-readback-", dir=os.environ.get("TMPDIR") or None))
os.environ["XDG_CONFIG_HOME"] = str(SCRATCH / "config")
sys.path[:0] = [str(ROOT), str(HERE)]

from pptmstr import boardlog, channels, settings  # noqa: E402

import proto_journal as proto  # noqa: E402

assert settings.config_dir().is_relative_to(SCRATCH), settings.config_dir()

FAILURES: list[str] = []

# The bytes HEAD 66af79e wrote for parts 2 and 5, kept so that a later build's
# writer is compared against HEAD's and not against itself: one JSON object, log
# label to file name to the file's text (every record file is ASCII, since the
# encoder escapes). ``--write-golden`` writes it; no other run writes here.
GOLDEN = HERE / "golden.json"
WRITE_GOLDEN = False


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def against_golden(label: str, written: dict[str, bytes]) -> None:
    stored = json.loads(GOLDEN.read_text("ascii")) if GOLDEN.exists() else {}
    if WRITE_GOLDEN:
        assert label not in stored, f"golden {label} is already written"
        stored[label] = {n: data.decode("ascii") for n, data in written.items()}
        GOLDEN.write_text(json.dumps(stored, indent=1, sort_keys=True) + "\n", "ascii")
        print(f"  wrote {len(written)} files as {GOLDEN.name}[{label}]")
        return
    want = {n: t.encode("ascii") for n, t in stored.get(label, {}).items()}
    check(
        f"what this build wrote is byte-for-byte golden[{label}] (HEAD 66af79e's)",
        bool(want) and written == want,
        f"{len(written)} files",
    )


def files_of(directory: Path) -> dict[str, bytes]:
    return {
        n: (directory / n).read_bytes()
        for n in sorted(os.listdir(directory))
        if not (directory / n).is_symlink() and (directory / n).is_file()
    }


BOARD = proto.kinds(*get_args(boardlog.Known))
LINKS = proto.kinds()
Fact = channels.Fact


# -- 1. the changed bodies are HEAD's, but for the parameter --------------------------


def _body(fn: Any) -> str:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    body = func.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return "\n".join(ast.unparse(stmt) for stmt in body)


SUBSTITUTIONS = (
    ("kinds.by_name.get(", "_KINDS.get("),
    ("kinds.hints[cls]", "_HINTS[cls]"),
    ("decode(text, kinds)", "decode(text)"),
    ("/ name(ordinal)", "/ _name(ordinal)"),
)


def part1() -> None:
    print("1. proto bodies against HEAD's, after the stated substitutions")
    for ours, head in ((proto.decode, boardlog.decode), (proto.read, boardlog.read)):
        text = _body(ours)
        for new, old in SUBSTITUTIONS:
            text = text.replace(new, old)
        check(f"{ours.__name__}: body equals HEAD's", text == _body(head))


# -- records: one or more of every kind, over awkward values -------------------------

TEXTS = (
    "",
    "plain",
    "ünïcödé",
    "astral \U0001f600",
    "lone \udc80 surrogate",
    "nul\x00inside",
    'quote " and back \\ slash',
    "line\nbreak\r\ttab",
    "\\u0000 written out",
)
TIMES = (1_762_500_000.125, 0.0, -1.5, 1e-300, 1_762_500_000.0)
INTS = (0, 7, 2**63, -1)
LISTS: tuple[tuple[str, ...], ...] = ((), ("a",), ("x", "\udc80", "nul\x00"), ("",))


def value(hint: Any, i: int) -> Any:
    if hint is str:
        return TEXTS[i % len(TEXTS)]
    if hint == str | None:
        return None if i % 3 == 0 else TEXTS[(i + 4) % len(TEXTS)]
    if hint is float:
        return TIMES[i % len(TIMES)]
    if hint is int:
        return INTS[i % len(INTS)]
    if hint is bool:
        return i % 2 == 0
    if hint == bool | None:
        return (None, True, False)[i % 3]
    return LISTS[i % len(LISTS)]


def board_records() -> list[Any]:
    out: list[Any] = []
    for i in range(6):
        for cls in (*get_args(boardlog.Known), boardlog.RecordingStopped):
            hints = get_type_hints(cls)
            out.append(
                cls(
                    **{
                        f.name: value(hints[f.name], i + k)
                        for k, f in enumerate(dataclasses.fields(cls))
                    }
                )
            )
    out.append(
        boardlog.Unrecognised(
            at=1.0,
            kind="a-later-kind",
            text='{"kind": "a-later-kind", "at": 1.0, "x": [1, {"y": null}]}',
        )
    )
    return out


# -- 2-3. a board log written by HEAD, read both ways ---------------------------------


def part2_3() -> None:
    print("2. a board log HEAD wrote: every kind, awkward values")
    records = board_records()
    a = SCRATCH / "board-a"
    for r in records:
        w = boardlog.append(a, r)
        assert isinstance(w, boardlog.Written) and w.gap is None, w
    before = files_of(a)
    against_golden("board", before)
    head = boardlog.read(a)
    ours = proto.read(a, BOARD)
    check(
        f"HEAD read is whole and returns what was appended ({len(records)} records)",
        head == boardlog.Log(tuple(records)),
    )
    check("proposed read equals HEAD read (records and damage)", ours == head)
    check("reading changed no file", files_of(a) == before)
    reencoded = [boardlog.encode(r).encode("utf-8") for r in ours.records]
    check(
        "each record read back re-encodes to its file's bytes", reencoded == list(before.values())
    )
    b = SCRATCH / "board-b"
    for r in ours.records:
        boardlog.append(b, r)
    check(
        "a log appended from the proposed read is byte-for-byte HEAD's (names and bytes)",
        files_of(b) == before,
        f"{len(before)} files, {sum(map(len, before.values()))} bytes",
    )
    kinds_seen = {type(r).__name__ for r in head.records}
    check(
        "every Known kind and RecordingStopped and Unrecognised appear",
        len(kinds_seen) == len(get_args(boardlog.Known)) + 2,
        str(len(kinds_seen)),
    )

    print("3. hand-written files no current append produces (older or foreign writers)")
    c = SCRATCH / "board-c"
    c.mkdir()
    texts = [
        # keys out of order, spaces, an integer time, an extra key
        '{ "at" : 1700000000 , "kind" : "task-declared", "task_id": "t1", "title": "T", "declared_by": "lead",'
        ' "detail": "d", "depends_on": [], "touches": [], "extra": {"a": 1} }\n',
        # every defaulted field left out
        '{"kind": "recording-stopped", "at": 2}\n',
        # non-ASCII written raw, not escaped
        '{"kind": "concern-posted", "at": 3.5, "concern_id": "c1", "sender": "ü", "recipient": "lead",'
        ' "subject": "s", "body": "b"}\n',
        # a kind this build does not define, odd whitespace kept
        '{"kind":"link-created","at":4,"channel_id":"x","members":["a","b"],"charter":""}\n',
    ]
    for i, t in enumerate(texts):
        (c / boardlog._name(i)).write_bytes(t.encode("utf-8"))
    head = boardlog.read(c)
    ours = proto.read(c, BOARD)
    check(
        "proposed read equals HEAD read",
        ours == head,
        f"HEAD: {len(head.records)} records, damage {head.damage}",
    )


# -- 4. damage: the same break, the same words ----------------------------------------


def damaged_logs() -> dict[str, Path]:
    good = boardlog.encode(boardlog.RecordingStopped(at=1.0, reason="r")).encode()
    cases: dict[str, Path] = {}

    def log(label: str, files: dict[int, bytes | None]) -> Path:
        d = SCRATCH / "damage" / label
        d.mkdir(parents=True)
        for ordinal, data in files.items():
            if data is not None:
                (d / boardlog._name(ordinal)).write_bytes(data)
        cases[label] = d
        return d

    log("torn", {0: good, 1: good[:-1], 2: good})
    log("missing ordinal", {0: good, 2: good, 3: good})
    log("not json", {0: good, 1: b"{nope\n"})
    log("not an object", {0: b"[1]\n"})
    log("no kind", {0: b'{"at": 1}\n'})
    log("time not a number", {0: b'{"kind": "recording-stopped", "at": "x"}\n'})
    log("time too large", {0: b'{"kind": "recording-stopped", "at": 1' + b"0" * 400 + b"}\n"})
    log(
        "field of the wrong shape",
        {0: b'{"kind": "task-claimed", "at": 1, "task_id": 5, "by": "x"}\n'},
    )
    log("required field missing", {0: b'{"kind": "task-claimed", "at": 1}\n'})
    log("not utf-8", {0: b'{"kind": "recording-stopped", "at": 1, "reason": "\xff"}\n'})
    log("nested too deep", {0: b"[" * 100_000 + b"]" * 100_000 + b"\n"})
    log("over the size limit", {0: b" " * (boardlog.MAX_RECORD_BYTES + 1) + b"\n"})
    d = log("a symlink under a record's name", {0: good})
    (d / boardlog._name(1)).symlink_to(d / boardlog._name(0))
    d = log("a directory under a record's name", {0: good})
    (d / boardlog._name(1)).mkdir()
    d = log("a fifo under a record's name", {0: good})
    os.mkfifo(d / boardlog._name(1))
    d = log("temp files and foreign names beside", {0: good, 1: good})
    (d / ".record-abc.tmp").write_bytes(b"half")
    (d / "000000002.json").write_bytes(good)
    (d / "notes.txt").write_bytes(b"x")
    d = log("unlistable", {0: good})
    d.chmod(0)
    return cases


def part4() -> None:
    print("4. damaged board logs: the same break and the same words")
    for label, d in damaged_logs().items():
        try:
            head, ours = boardlog.read(d), proto.read(d, BOARD)
        finally:
            d.chmod(0o755)
        check(f"{label}", ours == head, f"damage {head.damage}" if head.damage else "no damage")


# -- 5-6. channel logs ---------------------------------------------------------------


def proposed_channel_read(base: Path, link: channels.LinkKey) -> channels.Read:
    """
    ``FileChannels.read`` with the two substitutions the design makes: the
    journal's ``read`` under the link table, and its ``name``. The rest is HEAD's
    body, checked against it below.
    """
    directory = base / link.name / link.link_id
    log = proto.read(directory, LINKS)
    if log.damage is not None and not log.records and log.damage.ordinal == 0:
        try:
            os.listdir(directory)
        except FileNotFoundError:
            pass
        except OSError as exc:
            return channels.Read(
                broken=f"its directory cannot be listed: {type(exc).__name__}: {exc}"
            )
    facts: list[Fact] = []
    for position, record in enumerate(log.records):
        fact = channels.decode(record)
        if isinstance(fact, channels.Malformed):
            return channels.Read(
                tuple(facts),
                channels._broken(position, fact.reason, channels._withheld_after(position, log)),
                damaged=True,
            )
        facts.append(fact)
    if log.damage is None:
        return channels.Read(tuple(facts))
    damaged = log.damage.reason == "missing" or not channels._cannot_be_read(
        directory / proto.name(log.damage.ordinal)
    )
    return channels.Read(
        tuple(facts),
        channels._broken(log.damage.ordinal, log.damage.reason, log.damage.unread),
        damaged,
    )


def part5_6() -> None:
    print("5. channel logs HEAD wrote, through FileChannels.append")
    head_body = _body(channels.FileChannels.read)
    ours_body = _body(proposed_channel_read)
    for new, old in (
        (
            "directory = base / link.name / link.link_id",
            "directory = self._directory(link)\nif directory is None:\n    return Read(broken=_NOT_A_KEY)",
        ),
        ("proto.read(directory, LINKS)", "boardlog.read(directory)"),
        ("channels.", ""),
        ("proto.name(", "boardlog._name("),
    ):
        ours_body = ours_body.replace(new, old)
    check(
        "proposed channel read body is HEAD's FileChannels.read but for the path",
        ours_body == head_body,
    )

    base = SCRATCH / "channels"
    store = channels.FileChannels(base)
    link = channels.LinkKey("ops", "0b3e6c1e-4f0e-4a39-9b0c-6a1d1f1c0001")
    facts: list[Fact] = []
    for i, charter in enumerate(TEXTS):
        facts.append(
            channels.LinkCreated(
                at=TIMES[i % len(TIMES)],
                channel_id=f"c{i}",
                members=LISTS[i % len(LISTS)] or ("a", "b"),
                charter=charter,
            )
        )
        facts.append(channels.LinkClosed(at=TIMES[(i + 1) % len(TIMES)], channel_id=f"c{i}"))
    facts.append(boardlog.RecordingStopped(at=9.0, reason="stopped \udc80"))
    for f in facts:
        assert isinstance(store.append(link, f), channels.Appended)
    directory = base / link.name / link.link_id
    before = files_of(directory)
    against_golden("channel", before)
    head = store.read(link)
    ours = proposed_channel_read(base, link)
    check(
        f"HEAD read returns the facts appended ({len(facts)})", head == channels.Read(tuple(facts))
    )
    check("proposed read equals HEAD read", ours == head)
    expected = [boardlog.encode(channels.encode(f)).encode() for f in facts]
    check("each file is the fact's encoding, byte for byte", list(before.values()) == expected)
    check("reading changed no file", files_of(directory) == before)
    check(
        "judge agrees",
        channels.judge(link, ours) == channels.judge(link, head),
        str(channels.judge(link, head)),
    )

    print("   damaged channel logs")
    cases = {
        "members not a list": b'{"kind": "link-created", "at": 1, "channel_id": "c", "members": "ab", "charter": ""}\n',
        "charter missing": b'{"kind": "link-created", "at": 1, "channel_id": "c", "members": ["a", "b"]}\n',
        "time not a number": b'{"kind": "link-closed", "at": null, "channel_id": "c"}\n',
        "torn": b'{"kind": "link-closed", "at": 1, "channel_id": "c"}',
    }
    good = boardlog.encode(
        channels.encode(
            channels.LinkCreated(at=1.0, channel_id="c", members=("a", "b"), charter="")
        )
    ).encode()
    for label, data in cases.items():
        k = channels.LinkKey("dmg", label.replace(" ", "-"))
        d = base / k.name / k.link_id
        d.mkdir(parents=True)
        (d / boardlog._name(0)).write_bytes(good)
        (d / boardlog._name(1)).write_bytes(data)
        (d / boardlog._name(2)).write_bytes(good)
        h, o = store.read(k), proposed_channel_read(base, k)
        check(label, o == h, h.broken or "")

    print("6. board records in a channel's directory (the predicted difference)")
    k = channels.LinkKey("foreign", "board-records")
    d = base / k.name / k.link_id
    d.mkdir(parents=True)
    canonical = boardlog.encode(boardlog.Claimed(at=1.0, task_id="t", by="b")).encode()
    noncanonical = b'{"at": 2, "kind": "task-claimed", "task_id": "t", "by": "b", "extra": 1}\n'
    (d / boardlog._name(0)).write_bytes(good)
    (d / boardlog._name(1)).write_bytes(canonical)
    (d / boardlog._name(2)).write_bytes(noncanonical)
    h, o = store.read(k), proposed_channel_read(base, k)
    same_kind_and_at = [(type(x).__name__, x.at, getattr(x, "kind", None)) for x in h.facts] == [
        (type(x).__name__, x.at, getattr(x, "kind", None)) for x in o.facts
    ]
    check("same facts by type, kind and time", same_kind_and_at)
    check("judge agrees", channels.judge(k, o) == channels.judge(k, h), str(channels.judge(k, h)))
    on_disk = [(d / boardlog._name(i)).read_bytes()[:-1].decode() for i in range(3)]
    check(
        "proposed: each board record is carried as the file's own text",
        [x.text for x in o.facts[1:]] == on_disk[1:],
    )
    check(
        "HEAD: the non-canonical one is carried re-encoded, not as written (the predicted difference)",
        h.facts[2].text != on_disk[2] and h.facts[1].text == on_disk[1],
    )
    for i, (x, y) in enumerate(zip(h.facts, o.facts)):
        if x != y:
            print(f"  diff fact {i}: HEAD Unrecognised.text={x.text!r}")
            print(f"  {'':>11}proposed      text={y.text!r}")
            print(
                f"  {'':>11}file bytes          ={(d / boardlog._name(i)).read_bytes()[:-1].decode()!r}"
            )


# -- --existing: every log under a directory, read only --------------------------------


def existing(top: Path) -> None:
    print(f"7. existing logs under {top} (read only)")
    n = 0
    for here, dirs, names in os.walk(top, followlinks=False):
        if not boardlog._ordinals(names):
            continue
        d = Path(here)
        n += 1
        before = files_of(d)
        if "channels" in d.relative_to(top).parts and len(d.relative_to(top).parts) >= 3:
            k = channels.LinkKey(d.parent.name, d.name)
            base = d.parent.parent
            h, o = channels.FileChannels(base).read(k), proposed_channel_read(base, k)
        else:
            h, o = boardlog.read(d), proto.read(d, BOARD)
        check(str(d.relative_to(top)), o == h and files_of(d) == before)
    print(f"  {n} log directories")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--existing", type=Path)
    parser.add_argument("--write-golden", action="store_true")
    args = parser.parse_args()
    global WRITE_GOLDEN
    WRITE_GOLDEN = args.write_golden
    print(f"scratch: {SCRATCH}")
    print(f"python {sys.version.split()[0]}")
    part1()
    part2_3()
    part4()
    part5_6()
    if args.existing is not None:
        existing(args.existing.resolve())
    print(f"\n{len(FAILURES)} failed" + (": " + "; ".join(FAILURES) if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
