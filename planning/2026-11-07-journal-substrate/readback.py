"""
B1's condition, measured after the build: logs read back through ``pptmstr.journal``
to what the tree before B1 reads from the same files, and what this build writes is
byte-for-byte what that tree wrote.

Run from the repository root:

    .venv/bin/python planning/2026-11-07-journal-substrate/readback.py
    .venv/bin/python planning/2026-11-07-journal-substrate/readback.py --existing DIR
    .venv/bin/python planning/2026-11-07-journal-substrate/readback.py --build-tree DIR

**Two trees, one set of files.** The fixtures are written once, by this build. The
tree before B1 (``--head-rev``, default ``PRE_B1``) is extracted with ``git archive``
into ``$TMPDIR`` and each tree reads every fixture in a process of its own, this
script run as ``--observe`` with that tree first on ``sys.path``. Each observation is
a ``repr``: every value compared is a frozen dataclass of text, numbers, tuples and
None, whose ``repr`` names no module, so the two trees' values compare as text.

**Bytes against golden.json**, which holds what the tree at 66af79e wrote for parts 2
and 5 (``boardlog.py`` and ``channels.py`` did not change between 66af79e and the
tree before B1, apart from 1460798's move of ``append`` onto ``create_once``, which
golden.json was checked against at the time). A writer compared with itself would
pass whatever it wrote.

**Channels read under the board's kind table**, as the addendum of
``2026-11-07-the-board-log-is-one-journal-of-two.md`` requires, so part 6 and part
6b expect no difference at all: the design-time version of this script pinned the
difference the link-only table made, and that table was not built.

Every log this writes is under a fresh directory in ``$TMPDIR``, and
``XDG_CONFIG_HOME`` is pointed there before pptmstr is imported, so nothing under
``~/.config`` is read or written. ``--existing DIR`` additionally reads, and never
writes, every log directory under ``DIR`` through both trees; point it at a *copy*
of the operator's configuration directory.

``--build-tree DIR`` measures a tree other than this repository's as the build: a
copy with a deliberate defect, to show that a check fails.

Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

# The commit B1 was built on: the last tree whose ``boardlog`` reads the disk.
PRE_B1 = "3d57bb4"

# The bytes HEAD 66af79e wrote for parts 2 and 5: one JSON object, log label to
# file name to the file's text (every record file is ASCII, since the encoder
# escapes).
GOLDEN = HERE / "golden.json"


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--existing", type=Path)
    parser.add_argument("--build-tree", type=Path, default=ROOT)
    parser.add_argument("--head-rev", default=PRE_B1)
    parser.add_argument("--observe", nargs=2, type=Path, metavar=("MANIFEST", "OUT"))
    return parser.parse_args()


ARGS = _parse()
TREE = (ARGS.build_tree if ARGS.observe is None else Path(os.environ["READBACK_TREE"])).resolve()
SCRATCH = Path(tempfile.mkdtemp(prefix="b1-readback-", dir=os.environ.get("TMPDIR") or None))
os.environ["XDG_CONFIG_HOME"] = str(SCRATCH / "config")
sys.path[:0] = [str(TREE)]

from pptmstr import boardlog, channels, settings  # noqa: E402

assert Path(boardlog.__file__).resolve().is_relative_to(TREE), boardlog.__file__
assert settings.config_dir().is_relative_to(SCRATCH), settings.config_dir()

# The two trees' APIs: after B1 a read is handed the board's table; before, the
# table was a global of ``boardlog``.
try:
    from pptmstr.journal import codec, files

    def read_board(directory: Path) -> Any:
        return files.read(directory, boardlog.KINDS)

    encode: Any = codec.encode
    append: Any = files.append
    name: Any = files.name
    MAX_RECORD_BYTES: int = files.MAX_RECORD_BYTES
    RecordingStopped: Any = codec.RecordingStopped
    Unrecognised: Any = codec.Unrecognised
    Log: Any = codec.Log
    Written: Any = files.Written
    ORDINALS: Any = files._ordinals
except ImportError:

    def read_board(directory: Path) -> Any:
        return boardlog.read(directory)  # type: ignore[attr-defined]

    encode = boardlog.encode  # type: ignore[attr-defined]
    append = boardlog.append  # type: ignore[attr-defined]
    name = boardlog._name  # type: ignore[attr-defined]
    MAX_RECORD_BYTES = boardlog.MAX_RECORD_BYTES  # type: ignore[attr-defined]
    RecordingStopped = boardlog.RecordingStopped  # type: ignore[attr-defined]
    Unrecognised = boardlog.Unrecognised  # type: ignore[attr-defined]
    Log = boardlog.Log  # type: ignore[attr-defined]
    Written = boardlog.Written  # type: ignore[attr-defined]
    ORDINALS = boardlog._ordinals  # type: ignore[attr-defined]

from typing import get_args, get_type_hints  # noqa: E402

Fact = channels.Fact
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def files_of(directory: Path) -> dict[str, bytes]:
    return {
        n: (directory / n).read_bytes()
        for n in sorted(os.listdir(directory))
        if not (directory / n).is_symlink() and (directory / n).is_file()
    }


# -- the observer: one tree's reading of every fixture ---------------------------------


def _sweep(base: Path, name_: str, link_id: str) -> dict[str, str]:
    """
    Read, judge and ``owes_close`` for one link's log, then relink its name through
    a real ``ChannelWriter`` and say which files the sweep added. A close is named by
    its kind and its channel, since the sweep stamps it with the clock.
    """
    k = channels.LinkKey(name_, link_id)
    d = base / name_ / link_id
    store = channels.FileChannels(base)
    read = store.read(k)
    judged = channels.judge(k, read)
    owed = channels.owes_close(k, read)
    before = files_of(d)
    writer = channels.ChannelWriter(store)
    writer.submit(
        channels.LinkKey(name_, "later"),
        channels.LinkCreated(at=10.0, channel_id="later", members=("a", "b"), charter=""),
    )
    writer.close(timeout=10.0)
    after = files_of(d)
    assert all(before[n] == after[n] for n in before), "a record file was rewritten"
    added = [
        f"{p['kind']}({p.get('channel_id')})"
        for p in (json.loads(after[n]) for n in after if n not in before)
    ]
    return {
        "read": repr(read),
        "judge": repr(judged),
        "owes_close": repr(owed),
        "sweep appended": repr(added),
    }


def observe(manifest: Path, out: Path) -> int:
    entries = json.loads(manifest.read_text())
    seen: dict[str, dict[str, str]] = {}
    for label, entry in entries.items():
        path = Path(entry["path"])
        if entry["type"] == "board":
            seen[label] = {"read": repr(read_board(path))}
        elif entry["type"] == "channel":
            k = channels.LinkKey(entry["name"], entry["link_id"])
            read = channels.FileChannels(path).read(k)
            seen[label] = {
                "read": repr(read),
                "judge": repr(channels.judge(k, read)),
                "owes_close": repr(channels.owes_close(k, read)),
            }
        else:
            mine = SCRATCH / "sweep" / str(len(seen))
            shutil.copytree(path, mine, symlinks=True)
            seen[label] = _sweep(mine, entry["name"], entry["link_id"])
    out.write_text(json.dumps(seen, indent=1))
    return 0


# -- the fixtures, written by the build ---------------------------------------------

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
        for cls in (*get_args(boardlog.Known), RecordingStopped):
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
        Unrecognised(
            at=1.0,
            kind="a-later-kind",
            text='{"kind": "a-later-kind", "at": 1.0, "x": [1, {"y": null}]}',
        )
    )
    return out


class Fixtures:
    def __init__(self) -> None:
        self.root = SCRATCH / "fixtures"
        self.entries: dict[str, dict[str, str]] = {}

    def board(self, label: str) -> Path:
        d = self.root / "board" / str(len(self.entries))
        d.mkdir(parents=True)
        self.entries[label] = {"type": "board", "path": str(d)}
        return d

    def channel(self, label: str, kind: str, link: channels.LinkKey) -> Path:
        base = self.root / kind / str(len(self.entries))
        d = base / link.name / link.link_id
        d.mkdir(parents=True)
        self.entries[label] = {
            "type": kind,
            "path": str(base),
            "name": link.name,
            "link_id": link.link_id,
        }
        return d


def against_golden(label: str, written: dict[str, bytes]) -> None:
    stored = json.loads(GOLDEN.read_text("ascii"))
    want = {n: t.encode("ascii") for n, t in stored.get(label, {}).items()}
    check(
        f"what this build wrote is byte-for-byte golden[{label}] (66af79e's)",
        bool(want) and written == want,
        f"{len(written)} files",
    )


def part2_3(fx: Fixtures) -> list[Any]:
    print("2. a board log this build wrote: every kind, awkward values")
    records = board_records()
    a = fx.board("2. every kind")
    for r in records:
        w = append(a, r)
        assert isinstance(w, Written) and w.gap is None, w
    before = files_of(a)
    against_golden("board", before)
    ours = read_board(a)
    check(
        f"this build reads back what it appended ({len(records)} records)",
        ours == Log(tuple(records)),
    )
    reencoded = [encode(r).encode("utf-8") for r in ours.records]
    check(
        "each record read back re-encodes to its file's bytes", reencoded == list(before.values())
    )
    b = SCRATCH / "board-b"
    for r in ours.records:
        append(b, r)
    check(
        "a log appended from what was read is byte-for-byte the first (names and bytes)",
        files_of(b) == before,
        f"{len(before)} files, {sum(map(len, before.values()))} bytes",
    )
    kinds_seen = {type(r).__name__ for r in ours.records}
    check(
        "every Known kind and RecordingStopped and Unrecognised appear",
        len(kinds_seen) == len(get_args(boardlog.Known)) + 2,
        str(len(kinds_seen)),
    )

    c = fx.board("3. hand-written files no current append produces")
    texts = [
        # keys out of order, spaces, an integer time, an extra key
        '{ "at" : 1700000000 , "kind" : "task-declared", "task_id": "t1", "title": "T",'
        ' "declared_by": "lead", "detail": "d", "depends_on": [], "touches": [],'
        ' "extra": {"a": 1} }\n',
        # every defaulted field left out
        '{"kind": "recording-stopped", "at": 2}\n',
        # non-ASCII written raw, not escaped
        '{"kind": "concern-posted", "at": 3.5, "concern_id": "c1", "sender": "ü",'
        ' "recipient": "lead", "subject": "s", "body": "b"}\n',
        # a kind this build does not define, odd whitespace kept
        '{"kind":"link-created","at":4,"channel_id":"x","members":["a","b"],"charter":""}\n',
    ]
    for i, t in enumerate(texts):
        (c / name(i)).write_bytes(t.encode("utf-8"))
    return records


def damaged_logs(fx: Fixtures) -> list[Path]:
    good = encode(RecordingStopped(at=1.0, reason="r")).encode()
    locked: list[Path] = []

    def log(label: str, files_: dict[int, bytes | None]) -> Path:
        d = fx.board(f"4. {label}")
        for ordinal, data in files_.items():
            if data is not None:
                (d / name(ordinal)).write_bytes(data)
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
    log("over the size limit", {0: b" " * (MAX_RECORD_BYTES + 1) + b"\n"})
    d = log("a symlink under a record's name", {0: good})
    (d / name(1)).symlink_to(d / name(0))
    d = log("a directory under a record's name", {0: good})
    (d / name(1)).mkdir()
    d = log("a fifo under a record's name", {0: good})
    os.mkfifo(d / name(1))
    d = log("temp files and foreign names beside", {0: good, 1: good})
    (d / ".record-abc.tmp").write_bytes(b"half")
    (d / "000000002.json").write_bytes(good)
    (d / "notes.txt").write_bytes(b"x")
    d = log("unlistable", {0: good})
    d.chmod(0)
    locked.append(d)
    return locked


def part5_6(fx: Fixtures) -> None:
    print("5. a channel log this build wrote, through FileChannels.append")
    link = channels.LinkKey("ops", "0b3e6c1e-4f0e-4a39-9b0c-6a1d1f1c0001")
    directory = fx.channel("5. every link fact", "channel", link)
    store = channels.FileChannels(directory.parent.parent)
    facts: list[Any] = []
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
    facts.append(RecordingStopped(at=9.0, reason="stopped \udc80"))
    for f in facts:
        assert isinstance(store.append(link, f), channels.Appended)
    before = files_of(directory)
    against_golden("channel", before)
    check(
        f"this build reads back the facts appended ({len(facts)})",
        store.read(link) == channels.Read(tuple(facts)),
    )
    expected = [encode(channels.encode(f)).encode() for f in facts]
    check("each file is the fact's encoding, byte for byte", list(before.values()) == expected)

    good = encode(
        channels.encode(
            channels.LinkCreated(at=1.0, channel_id="c", members=("a", "b"), charter="")
        )
    ).encode()
    cases = {
        "members not a list": b'{"kind": "link-created", "at": 1, "channel_id": "c",'
        b' "members": "ab", "charter": ""}\n',
        "charter missing": b'{"kind": "link-created", "at": 1, "channel_id": "c",'
        b' "members": ["a", "b"]}\n',
        "time not a number": b'{"kind": "link-closed", "at": null, "channel_id": "c"}\n',
        "torn": b'{"kind": "link-closed", "at": 1, "channel_id": "c"}',
    }
    for label, data in cases.items():
        k = channels.LinkKey("dmg", label.replace(" ", "-"))
        d = fx.channel(f"5. damaged channel log: {label}", "channel", k)
        (d / name(0)).write_bytes(good)
        (d / name(1)).write_bytes(data)
        (d / name(2)).write_bytes(good)

    k = channels.LinkKey("foreign", "board-records")
    d = fx.channel("6. board records in a channel's directory", "channel", k)
    (d / name(0)).write_bytes(good)
    (d / name(1)).write_bytes(encode(boardlog.Claimed(at=1.0, task_id="t", by="b")).encode())
    (d / name(2)).write_bytes(
        b'{"at": 2, "kind": "task-claimed", "task_id": "t", "by": "b", "extra": 1}\n'
    )


def _bare(cls: type[Any]) -> bytes:
    return json.dumps({"kind": cls.KIND, "at": 1}).encode() + b"\n"


def _wrong_shape(cls: type[Any]) -> bytes | None:
    """``cls``'s record with its first required field given the wrong JSON type."""
    hints = get_type_hints(cls)
    payload: dict[str, Any] = {"kind": cls.KIND, "at": 1}
    target = None
    for f in dataclasses.fields(cls):
        if f.name == "at":
            continue
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            hint = hints[f.name]
            if target is None:
                target = f.name
                payload[f.name] = {"not": "this"}
            else:
                payload[f.name] = value(hint, 1)
                if isinstance(payload[f.name], tuple):
                    payload[f.name] = list(payload[f.name])
    if target is None:
        return None
    return json.dumps(payload).encode() + b"\n"


def _whole(cls: type[Any]) -> bytes:
    hints = get_type_hints(cls)
    record = cls(**{f.name: value(hints[f.name], 1) for f in dataclasses.fields(cls)})
    return encode(record).encode()


def part6b(fx: Fixtures) -> int:
    link_id = "earlier"
    k = channels.LinkKey("sweep", link_id)

    def fact(f: Any) -> bytes:
        return encode(channels.encode(f)).encode()

    created = fact(channels.LinkCreated(at=1.0, channel_id=link_id, members=("a", "b"), charter=""))
    closed = fact(channels.LinkClosed(at=2.0, channel_id=link_id))
    unpaired = fact(channels.LinkCreated(at=1.0, channel_id=link_id, members=("a",), charter=""))
    stopped = encode(RecordingStopped(at=3.0, reason="r")).encode()
    skeptic = b'{"kind":"task-claimed","at":1}\n'

    cases: dict[str, list[bytes]] = {
        "skeptic's record alone": [skeptic],
        "created, then the skeptic's record": [created, skeptic],
        "the skeptic's record, then created": [skeptic, created],
        "created, skeptic's record, closed": [created, skeptic, closed],
        "unpaired creation, then the skeptic's record": [unpaired, skeptic],
        "created, skeptic's record, stop marker": [created, skeptic, stopped],
        "created, a well-formed task-claimed": [created, _whole(boardlog.Claimed)],
        "created, a malformed recording-stopped (reason 5)": [
            created,
            b'{"kind": "recording-stopped", "at": 1, "reason": 5}\n',
        ],
        "created, a malformed board record with no at": [created, b'{"kind": "task-claimed"}\n'],
        "created, an unknown kind": [created, b'{"kind": "a-later-kind", "at": 1}\n'],
    }
    for cls in get_args(boardlog.Known):
        cases[f"created, bare {cls.KIND}"] = [created, _bare(cls)]
        wrong = _wrong_shape(cls)
        if wrong is not None:
            cases[f"created, {cls.KIND} with a field of the wrong shape"] = [created, wrong]
        cases[f"created, well-formed {cls.KIND}"] = [created, _whole(cls)]
    for label, data in cases.items():
        d = fx.channel(f"6b. {label}", "sweep", k)
        for i, each in enumerate(data):
            (d / name(i)).write_bytes(each)
    return len(cases)


# -- --existing: every log under a directory, read only --------------------------------


def existing(fx: Fixtures, top: Path) -> None:
    for here, _dirs, names in os.walk(top, followlinks=False):
        if not ORDINALS(names):
            continue
        d = Path(here)
        rel = d.relative_to(top)
        label = f"7. {rel}"
        if "channels" in rel.parts and len(rel.parts) >= 3:
            fx.entries[label] = {
                "type": "channel",
                "path": str(d.parent.parent),
                "name": d.parent.name,
                "link_id": d.name,
            }
        else:
            fx.entries[label] = {"type": "board", "path": str(d)}


# -- the two trees -----------------------------------------------------------------


def head_tree(rev: str) -> Path:
    out = SCRATCH / f"tree-{rev}"
    out.mkdir()
    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", rev, "pptmstr"], check=True, capture_output=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(out)], input=archive, check=True)
    return out


def run_observer(tree: Path, manifest: Path, side: str) -> dict[str, dict[str, str]]:
    out = SCRATCH / f"observed-{side}.json"
    env = {**os.environ, "READBACK_TREE": str(tree), "PYTHONPATH": ""}
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--observe", str(manifest), str(out)],
        env=env,
        check=True,
        cwd=SCRATCH,
    )
    observed: dict[str, dict[str, str]] = json.loads(out.read_text())
    return observed


def main() -> int:
    if ARGS.observe is not None:
        return observe(*ARGS.observe)
    print(f"scratch: {SCRATCH}")
    print(f"python {sys.version.split()[0]}")
    print(f"build: {TREE}; before B1: {ARGS.head_rev}")
    fx = Fixtures()
    part2_3(fx)
    locked = damaged_logs(fx)
    part5_6(fx)
    variants = part6b(fx)
    if ARGS.existing is not None:
        existing(fx, ARGS.existing.resolve())
    manifest = SCRATCH / "manifest.json"
    manifest.write_text(json.dumps(fx.entries, indent=1))
    before = {
        label: files_of(Path(e["path"]))
        for label, e in fx.entries.items()
        if e["type"] == "board" and Path(e["path"]) not in locked
    }

    try:
        head = run_observer(head_tree(ARGS.head_rev), manifest, "head")
        ours = run_observer(TREE, manifest, "build")
    finally:
        for d in locked:
            d.chmod(0o755)

    print("2-7. every fixture, read by both trees")
    section = ""
    differ_6b: list[str] = []
    for label in fx.entries:
        now = label.split(" ", 1)[0]
        if now != section and now != "6b.":
            section = now
            print(f" {section}")
        h, o = head[label], ours[label]
        same = h == o
        if label.startswith("6b."):
            if not same:
                differ_6b.append(label)
            continue
        detail = (
            ""
            if same
            else "; ".join(f"{key}: {h[key]} vs {o[key]}" for key in h if h[key] != o.get(key))
        )
        check(label, same, detail[:400])
    print(f" 6b. {variants} variants: read, judge, owes_close, and what the sweep appends")
    for label in differ_6b:
        h, o = head[label], ours[label]
        print(f"    differs: {label}")
        for key in h:
            if h[key] != o.get(key):
                print(f"      {key}: before B1 {h[key]}  build {o.get(key)}")
    check(
        f"no 6b variant differs between the trees ({len(differ_6b)} of {variants})",
        not differ_6b,
    )
    check(
        "reading changed no board fixture",
        all(files_of(Path(fx.entries[label]["path"])) == files for label, files in before.items()),
    )
    print(f"\n{len(FAILURES)} failed" + (": " + "; ".join(FAILURES) if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
