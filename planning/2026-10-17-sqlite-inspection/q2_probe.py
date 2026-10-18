#!/usr/bin/env python3
"""
Q2 of the SQLite inspection: measure what this interpreter's sqlite3 does.

Stdlib only. Every database and record directory lives in a fresh directory
under $TMPDIR, removed at the end unless --keep is given. Run it as

    /home/wreel/Source/pptmstr/.venv/bin/python q2_probe.py [--mypy PATH]

Sections, in order: versions and filesystems; two writer processes on one
database; SIGKILL inside a multi-row write, SQLite against boardlog's
link-per-record; Python's transaction-control quirks; schema growth against an
older reader; inspectability.

Worker modes (--worker ...) are the same file run again as a separate OS
process, so every writer is its own process with its own SQLite connection.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import random
import shutil
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PY = sys.executable
HERE = Path(__file__).resolve()
RECORD_END = "END\n"


def out(line: str = "") -> None:
    print(line, flush=True)


# --------------------------------------------------------------------------- #
# Filesystems
# --------------------------------------------------------------------------- #


def fstype(path: Path) -> str:
    """
    The mount point and filesystem type /proc/mounts gives for the longest
    mount prefix of path.
    """
    best = ("", "?")
    real = str(path.resolve())
    with open("/proc/mounts", encoding="utf-8") as handle:
        for line in handle:
            parts = line.split()
            mount, kind = parts[1], parts[2]
            if (real == mount or real.startswith(mount.rstrip("/") + "/")) and len(mount) >= len(
                best[0]
            ):
                best = (mount, kind)
    return f"{best[1]} (mounted at {best[0]})"


# --------------------------------------------------------------------------- #
# 1. Versions
# --------------------------------------------------------------------------- #

MYPY_312_ONLY = """\
import sqlite3
conn = sqlite3.connect(":memory:", autocommit=False)
print(conn.autocommit)
"""


def section_versions(work: Path, mypy: str | None) -> None:
    out("== 1. Versions")
    out(f"python            {sys.version.split()[0]} ({sys.executable})")
    out(f"sqlite library    {sqlite3.sqlite_version}")
    out(f"sqlite3.threadsafety {sqlite3.threadsafety}")
    out(f"Connection.autocommit attribute (3.12+): {hasattr(sqlite3.Connection, 'autocommit')}")
    out(
        f"sqlite3.LEGACY_TRANSACTION_CONTROL (3.12+): {hasattr(sqlite3, 'LEGACY_TRANSACTION_CONTROL')}"
    )
    conn = sqlite3.connect(":memory:")
    opts = [r[0] for r in conn.execute("PRAGMA compile_options")]
    conn.close()
    keep = (
        "THREADSAFE",
        "DEFAULT_SYNCHRONOUS",
        "DEFAULT_WAL_SYNCHRONOUS",
        "ENABLE_FTS",
        "ENABLE_JSON",
        "DEFAULT_JOURNAL",
    )
    out("compile_options   " + ", ".join(o for o in opts if o.startswith(keep)))
    probe = sqlite3.connect(str(work / "versions.db"))
    out(
        f"defaults on a new file: journal_mode={probe.execute('PRAGMA journal_mode').fetchone()[0]}"
        f" synchronous={probe.execute('PRAGMA synchronous').fetchone()[0]}"
        f" busy_timeout={probe.execute('PRAGMA busy_timeout').fetchone()[0]}ms"
        f" (connect() default timeout=5.0s)"
    )
    probe.close()
    if mypy is None:
        out("mypy: not given (--mypy PATH), not checked")
        return
    cache = str(work / "mypy-cache")
    # Q4-J imports pptmstr, which has no py.typed, and the venv's editable
    # install points at the main checkout rather than this tree. Putting the
    # probe's own tree on MYPYPATH makes mypy analyse that tree's pptmstr as
    # source. --no-warn-unused-ignores is pyproject.toml's setting: pptmstr's
    # ignores are written against it, and --strict alone would report them.
    tree = HERE.parents[2]
    probe_env = {**os.environ, "MYPYPATH": str(tree)}
    for label, args, env in (
        (
            f"this probe (MYPYPATH={tree}, --no-warn-unused-ignores)",
            ["--no-warn-unused-ignores", str(HERE)],
            probe_env,
        ),
        ("a 3.12-only snippet (connect(autocommit=...))", ["-c", MYPY_312_ONLY], None),
    ):
        proc = subprocess.run(
            [
                mypy,
                "--python-version",
                "3.11",
                "--strict",
                "--cache-dir",
                cache,
                "--no-incremental",
                *args,
            ],
            capture_output=True,
            text=True,
            cwd=str(work),
            env=env,
        )
        lines = [
            ln for ln in (proc.stdout + proc.stderr).strip().splitlines() if " note: " not in ln
        ]
        out(f"mypy --python-version 3.11 --strict on {label}: exit {proc.returncode}")
        for line in lines:
            out(f"    {line}")


# --------------------------------------------------------------------------- #
# 2. Two writer processes
# --------------------------------------------------------------------------- #


def worker_writer(a: argparse.Namespace) -> None:
    """
    One writer: n read-modify-write transactions on a shared counter plus one
    inserted row each. The counter's new value is computed in Python from the
    value read, so a transaction that is not isolated loses an update without
    raising anything.
    """
    legacy = a.begin == "LEGACY"
    if legacy:
        conn = sqlite3.connect(a.db, timeout=a.busy_ms / 1000.0)
    else:
        conn = sqlite3.connect(a.db, timeout=a.busy_ms / 1000.0, isolation_level=None)
    acked = 0
    errors: dict[str, int] = {}
    waits: list[float] = []
    begins: list[float] = []
    failed_waits: list[float] = []
    while time.time() < a.start_at:
        time.sleep(0.001)
    for seq in range(a.n):
        if a.think_ms:
            time.sleep(a.think_ms / 1000.0)
        t0 = time.perf_counter()
        try:
            if not legacy:
                conn.execute(f"BEGIN {a.begin}")
            t1 = time.perf_counter()
            (value,) = conn.execute("SELECT v FROM counter WHERE id = 1").fetchone()
            conn.execute("INSERT INTO rows (wid, seq) VALUES (?, ?)", (a.wid, seq))
            conn.execute("UPDATE counter SET v = ? WHERE id = 1", (value + 1,))
            if legacy:
                conn.commit()
            else:
                conn.execute("COMMIT")
            acked += 1
            begins.append(t1 - t0)
            waits.append(time.perf_counter() - t0)
        except sqlite3.OperationalError as exc:
            errors[str(exc)] = errors.get(str(exc), 0) + 1
            failed_waits.append(time.perf_counter() - t0)
            if conn.in_transaction:
                if legacy:
                    conn.rollback()
                else:
                    conn.execute("ROLLBACK")
    conn.close()
    Path(a.result).write_text(
        json.dumps(
            {
                "acked": acked,
                "errors": errors,
                "waits": waits,
                "begins": begins,
                "failed_waits": failed_waits,
            }
        )
    )


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] * 1000.0


def section_writers(work: Path, n: int) -> None:
    out()
    out(f"== 2. Two writer processes, one database, {n} transactions each")
    out("   each transaction: SELECT counter; INSERT row; UPDATE counter = read+1; COMMIT")
    configs = [
        ("wal", "IMMEDIATE", 5000),
        ("delete", "IMMEDIATE", 5000),
        ("wal", "DEFERRED", 5000),
        ("delete", "DEFERRED", 5000),
        ("wal", "LEGACY", 5000),
        ("wal", "IMMEDIATE", 0),
        ("delete", "IMMEDIATE", 0),
    ]
    for journal, begin, busy in configs:
        db = work / f"writers-{journal}-{begin}-{busy}.db"
        setup = sqlite3.connect(str(db), isolation_level=None)
        setup.execute(f"PRAGMA journal_mode={journal}")
        setup.execute("CREATE TABLE rows (wid INTEGER, seq INTEGER)")
        setup.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, v INTEGER)")
        setup.execute("INSERT INTO counter VALUES (1, 0)")
        setup.close()
        start_at = time.time() + 1.0
        procs = []
        for wid in (1, 2):
            result = work / f"writer-{journal}-{begin}-{busy}-{wid}.json"
            procs.append(
                (
                    result,
                    subprocess.Popen(
                        [
                            PY,
                            str(HERE),
                            "--worker",
                            "writer",
                            "--db",
                            str(db),
                            "--begin",
                            begin,
                            "--busy-ms",
                            str(busy),
                            "--n",
                            str(n),
                            "--wid",
                            str(wid),
                            "--start-at",
                            repr(start_at),
                            "--result",
                            str(result),
                        ]
                    ),
                )
            )
        for _, proc in procs:
            proc.wait()
        results = [json.loads(r.read_text()) for r, _ in procs]
        check = sqlite3.connect(str(db))
        rows = check.execute("SELECT count(*) FROM rows").fetchone()[0]
        counter = check.execute("SELECT v FROM counter").fetchone()[0]
        check.close()
        acked = sum(r["acked"] for r in results)
        errors: dict[str, int] = {}
        for r in results:
            for k, v in r["errors"].items():
                errors[k] = errors.get(k, 0) + v
        waits = [w for r in results for w in r["waits"]]
        begins = [w for r in results for w in r["begins"]]
        label = "default isolation_level, no BEGIN" if begin == "LEGACY" else f"BEGIN {begin}"
        out(f"-- journal={journal:6} {label:34} busy_timeout={busy}ms")
        out(
            f"   acked commits {acked}/{2 * n}; rows present {rows}; lost writes (acked - rows) {acked - rows};"
            f" lost updates (rows - counter) {rows - counter}"
        )
        out(
            f"   errors: {errors if errors else 'none'}; acked per writer {[r['acked'] for r in results]}"
        )
        failed = [w for r in results for w in r["failed_waits"]]
        if failed:
            out(
                f"   a failed attempt waited ms p50 {pct(failed, 0.5):.2f} max {pct(failed, 1.0):.2f} before its error"
            )
        out(
            f"   commit latency ms p50 {pct(waits, 0.5):.2f} p99 {pct(waits, 0.99):.2f} max {pct(waits, 1.0):.2f};"
            f" BEGIN wait ms p50 {pct(begins, 0.5):.2f} p99 {pct(begins, 0.99):.2f} max {pct(begins, 1.0):.2f}"
        )


# --------------------------------------------------------------------------- #
# 3. SIGKILL inside a multi-row write
# --------------------------------------------------------------------------- #


def _log(fd: int, text: str) -> None:
    os.write(fd, text.encode())


def worker_crash_sqlite(a: argparse.Namespace) -> None:
    """
    Writes groups of k rows forever; a group is one transaction unless
    --begin NONE, in which case each INSERT autocommits (the wrong setup).
    The log is written with os.write, so it survives SIGKILL in the page cache.
    --row-sleep-ms holds the transaction open between INSERTs so a kill can land
    inside it; without it nearly every kill lands in COMMIT's fsyncs.
    """
    conn = sqlite3.connect(a.db, timeout=5.0, isolation_level=None)
    if a.autocheckpoint is not None:
        conn.execute(f"PRAGMA wal_autocheckpoint={a.autocheckpoint}")
    fd = os.open(a.log, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    g = (conn.execute("SELECT coalesce(max(g), -1) FROM rows").fetchone()[0]) + 1
    _log(fd, "R\n")
    while True:
        if a.begin != "NONE":
            conn.execute("BEGIN IMMEDIATE")
        _log(fd, f"B {g}\n")
        for j in range(a.k):
            conn.execute("INSERT INTO rows (g, j, pad) VALUES (?, ?, ?)", (g, j, "x" * 200))
            time.sleep(a.row_sleep_ms / 1000.0)
        _log(fd, f"c {g}\n")
        if a.begin != "NONE":
            conn.execute("COMMIT")
        _log(fd, f"C {g}\n")
        if a.checkpoint:
            # Q4 (S2 finding 11): an explicit checkpoint after every group,
            # bracketed in the log so a kill inside it is counted as one.
            _log(fd, f"k {g}\n")
            conn.execute(f"PRAGMA wal_checkpoint({a.checkpoint})").fetchall()
            _log(fd, f"K {g}\n")
        g += 1


def _ordinals(names: list[str]) -> list[int]:
    return sorted(int(n) for n in names if n.isdigit())


def worker_crash_files(a: argparse.Namespace) -> None:
    """
    boardlog.append's mechanism, re-implemented here so the probe stays stdlib
    and writes nothing under pptmstr/: list the directory, write a temp file,
    fsync it, os.link it to one past the highest ordinal, move up on
    FileExistsError, unlink the temp. A group is k such records in a row.
    """
    directory = Path(a.dir)
    fd = os.open(a.log, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
    groups = [
        int(p.read_text().split()[0])
        for p in directory.iterdir()
        if p.name.isdigit() and p.read_text().endswith(RECORD_END)
    ]
    g = max(groups, default=-1) + 1
    _log(fd, "R\n")
    while True:
        _log(fd, f"B {g}\n")
        for j in range(a.k):
            data = f"{g} {j} {'x' * 200} {RECORD_END}".encode()
            present = _ordinals(os.listdir(directory))
            ordinal = present[-1] + 1 if present else 0
            tfd, tmp = tempfile.mkstemp(dir=str(directory), prefix=".record-", suffix=".tmp")
            try:
                with os.fdopen(tfd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                while True:
                    try:
                        os.link(tmp, directory / f"{ordinal:08d}")
                        break
                    except FileExistsError:
                        ordinal += 1
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
        _log(fd, f"C {g}\n")
        g += 1


def _last_state(log: Path) -> tuple[set[int], str]:
    acked: set[int] = set()
    last = ""
    for line in log.read_text().splitlines():
        if line.startswith("C "):
            acked.add(int(line.split()[1]))
        last = line
    if last.startswith("B "):
        return acked, "inside the group"
    if last.startswith("c "):
        return acked, "inside COMMIT"
    if last.startswith("k "):
        return acked, "inside checkpoint"
    return acked, "between groups"


def _kill_once(cmd: list[str], log: Path, rng: random.Random) -> None:
    log.write_text("")
    proc = subprocess.Popen(cmd)
    deadline = time.time() + 10
    while "R\n" not in log.read_text():
        if time.time() > deadline:
            raise RuntimeError("worker never became ready")
        time.sleep(0.002)
    time.sleep(rng.uniform(0.005, 0.120))
    proc.send_signal(signal.SIGKILL)
    proc.wait()


def section_crash(
    work: Path,
    iterations: int,
    k: int,
    seed: int,
    row_sleep_ms: float,
    variants: list[tuple[str, str, str, tuple[str, ...]]] | None = None,
) -> None:
    out()
    out(
        f"== 3. SIGKILL during a {k}-row write, {iterations} kills per variant, kill 5-120 ms after ready"
    )
    out(
        f"   the SQLite writer sleeps {row_sleep_ms} ms after each INSERT so kills also land inside the open"
    )
    out(
        "   transaction and not only in COMMIT; the file writer's per-record fsync already spreads it"
    )
    if variants is None:
        variants = [
            ("sqlite", "wal", "IMMEDIATE", ()),
            ("sqlite", "delete", "IMMEDIATE", ()),
            ("sqlite", "wal", "NONE", ()),
            ("files", "-", "-", ()),
        ]
    for kind, journal, begin, extra in variants:
        rng = random.Random(seed)
        tag = "".join(extra).replace("-", "").replace("(", "").replace(")", "")
        log = work / f"crash-{kind}-{journal}-{begin}{tag}.log"
        states: dict[str, int] = {}
        partial_seen = 0
        torn = 0
        acked_missing = 0
        integrity_bad = 0
        sidecar_after_kill: dict[str, int] = {}
        tmp_left = 0
        commit_kills: dict[str, int] = {}
        if kind == "sqlite":
            db = work / f"crash-{journal}-{begin}{tag}.db"
            setup = sqlite3.connect(str(db), isolation_level=None)
            setup.execute(f"PRAGMA journal_mode={journal}")
            setup.execute("CREATE TABLE rows (g INTEGER, j INTEGER, pad TEXT)")
            setup.close()
            cmd = [
                PY,
                str(HERE),
                "--worker",
                "crash-sqlite",
                "--db",
                str(db),
                "--k",
                str(k),
                "--begin",
                begin,
                "--log",
                str(log),
                "--row-sleep-ms",
                str(row_sleep_ms),
                *extra,
            ]
        else:
            directory = work / "crash-files"
            directory.mkdir()
            cmd = [
                PY,
                str(HERE),
                "--worker",
                "crash-files",
                "--dir",
                str(directory),
                "--k",
                str(k),
                "--log",
                str(log),
            ]
        for _ in range(iterations):
            _kill_once(cmd, log, rng)
            acked, state = _last_state(log)
            states[state] = states.get(state, 0) + 1
            if kind == "sqlite":
                for suffix in ("-journal", "-wal"):
                    if (
                        Path(str(db) + suffix).exists()
                        and Path(str(db) + suffix).stat().st_size > 0
                    ):
                        sidecar_after_kill[suffix] = sidecar_after_kill.get(suffix, 0) + 1
                check = sqlite3.connect(str(db))
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    integrity_bad += 1
                counts = dict(check.execute("SELECT g, count(*) FROM rows GROUP BY g").fetchall())
                check.close()
            else:
                counts = {}
                for p in directory.iterdir():
                    if p.name.endswith(".tmp"):
                        continue
                    text = p.read_text()
                    if not text.endswith(RECORD_END):
                        torn += 1
                        continue
                    grp = int(text.split()[0])
                    counts[grp] = counts.get(grp, 0) + 1
            partial = [grp for grp, c in counts.items() if c != k]
            if partial:
                partial_seen += 1
            acked_missing += sum(1 for grp in acked if counts.get(grp, 0) != k)
            if state in ("inside COMMIT", "inside checkpoint"):
                killed = int(log.read_text().splitlines()[-1].split()[1])
                outcome = "group present whole" if counts.get(killed, 0) == k else "group absent"
                if state == "inside checkpoint":
                    outcome = "in checkpoint: " + outcome
                commit_kills[outcome] = commit_kills.get(outcome, 0) + 1
        if kind == "files":
            tmp_left = sum(1 for p in directory.iterdir() if p.name.endswith(".tmp"))
            total = sum(1 for p in directory.iterdir() if p.name.isdigit())
            final_partial = sum(1 for c in counts.values() if c != k)
            out(f"-- files (boardlog's tmp + fsync + os.link per record), {k} records per group")
            out(f"   kills landed: {states}")
            out(
                f"   torn record files {torn} (cumulative over checks); acked groups incomplete {acked_missing}"
            )
            out(
                f"   checks that showed a partial group: {partial_seen}/{iterations}; partial groups in the final directory {final_partial} of {len(counts)}"
            )
            out(
                f"   record files {total} ({total / iterations:.1f} per run); .record-*.tmp left behind {tmp_left}"
            )
        else:
            label = (
                "each INSERT autocommits (no BEGIN)"
                if begin == "NONE"
                else "one BEGIN IMMEDIATE transaction"
            )
            out(
                f"-- sqlite journal={journal}, {label} per group, synchronous default"
                + (f", worker args {' '.join(extra)}" if extra else "")
            )
            out(f"   kills landed: {states}")
            out(
                f"   integrity_check not ok: {integrity_bad}; acked groups incomplete {acked_missing}"
            )
            out(f"   checks that showed a partial group: {partial_seen}/{iterations}")
            if commit_kills:
                out(f"   a kill inside COMMIT (or checkpoint) left: {commit_kills}")
            out(
                f"   non-empty sidecar seen right after a kill: {sidecar_after_kill if sidecar_after_kill else 'none'}"
            )


# --------------------------------------------------------------------------- #
# 4. Transaction-control quirks
# --------------------------------------------------------------------------- #


class Boom(Exception):
    pass


def _fresh(path: Path, legacy: bool) -> sqlite3.Connection:
    """
    A new database with one empty table, opened in the default (legacy)
    transaction control when legacy, else with isolation_level=None.
    """
    if path.exists():
        path.unlink()
    setup = sqlite3.connect(str(path), isolation_level=None)
    setup.execute("CREATE TABLE t (x INTEGER)")
    setup.close()
    if legacy:
        return sqlite3.connect(str(path))
    return sqlite3.connect(str(path), isolation_level=None)


def _visible(path: Path) -> int:
    other = sqlite3.connect(str(path))
    n: int = other.execute("SELECT count(*) FROM t").fetchone()[0]
    other.close()
    return n


def section_quirks(work: Path) -> None:
    out()
    out("== 4. Python sqlite3 transaction control on this interpreter")
    path = work / "quirks.db"

    conn = _fresh(path, True)
    sent: list[str] = []
    conn.set_trace_callback(sent.append)
    out(f"default isolation_level = {conn.isolation_level!r}")
    conn.execute("SELECT count(*) FROM t").fetchone()
    out(f"   after SELECT: in_transaction={conn.in_transaction}; sent {sent}")
    sent.clear()
    conn.execute("INSERT INTO t VALUES (1)")
    out(f"   after INSERT: in_transaction={conn.in_transaction}; sent {sent}")
    conn.commit()
    sent.clear()
    conn.execute("CREATE TABLE u (y INTEGER)")
    out(f"   after CREATE TABLE: in_transaction={conn.in_transaction}; sent {sent}")
    conn.close()

    def attempt(label: str, legacy: bool, body: str) -> None:
        c = _fresh(path, legacy)
        try:
            if body == "with":
                with c:
                    c.execute("INSERT INTO t VALUES (1)")
                    c.execute("INSERT INTO t VALUES (2)")
                    raise Boom
            elif body == "begin":
                c.execute("BEGIN IMMEDIATE")
                try:
                    c.execute("INSERT INTO t VALUES (1)")
                    c.execute("INSERT INTO t VALUES (2)")
                    raise Boom
                except BaseException:
                    c.execute("ROLLBACK")
                    raise
            elif body == "executescript":
                with c:
                    c.execute("INSERT INTO t VALUES (1)")
                    c.executescript("INSERT INTO t VALUES (2);")
                    raise Boom
        except Boom:
            pass
        seen = _visible(path)
        c.close()
        verdict = "atomic" if seen == 0 else "NOT atomic"
        out(f"   {label}: rows another connection sees after the failure = {seen} -> {verdict}")

    out("two INSERTs then an exception; 0 rows visible afterwards means the write was atomic")
    attempt("default isolation_level, `with conn:`", True, "with")
    attempt("isolation_level=None, `with conn:` (no BEGIN)", False, "with")
    attempt("isolation_level=None, explicit BEGIN IMMEDIATE ... ROLLBACK", False, "begin")
    attempt("default isolation_level, `with conn:` around executescript", True, "executescript")


# --------------------------------------------------------------------------- #
# 5. Schema growth
# --------------------------------------------------------------------------- #


def section_schema(work: Path) -> Path:
    out()
    out("== 5. Schema growth: an older reader and writer against a newer database")
    path = work / "schema.db"
    v1 = sqlite3.connect(str(path), isolation_level=None)
    v1.execute("CREATE TABLE links (id TEXT PRIMARY KEY, a TEXT, b TEXT)")
    v1.execute("PRAGMA user_version = 1")
    v1.execute("INSERT INTO links VALUES ('L1', 'lead-a', 'lead-b')")
    v1.close()

    v2 = sqlite3.connect(str(path), isolation_level=None)
    v2.execute("BEGIN IMMEDIATE")
    v2.execute("ALTER TABLE links ADD COLUMN revoked_at TEXT DEFAULT NULL")
    v2.execute("CREATE TABLE decisions (link TEXT, text TEXT)")
    v2.execute("INSERT INTO links VALUES ('L2', 'lead-a', 'lead-c', '2026-10-17T12:00')")
    v2.execute("INSERT INTO decisions VALUES ('L1', 'deliverables go through shots/')")
    v2.execute("PRAGMA user_version = 2")
    v2.execute("COMMIT")
    v2.close()

    old = sqlite3.connect(str(path))
    explicit = old.execute("SELECT id, a, b FROM links ORDER BY id").fetchall()
    out(f"   v1 reader, explicit columns: {explicit}  <- L2 is revoked and reads as live")
    try:
        rows = old.execute("SELECT * FROM links ORDER BY id").fetchall()
        for link_id, a, b in rows:
            pass
        out("   v1 reader, SELECT * with 3-tuple unpack: worked")
    except ValueError as exc:
        out(f"   v1 reader, SELECT * with 3-tuple unpack: ValueError: {exc}")
    tables = [
        r[0] for r in old.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")
    ]
    version = old.execute("PRAGMA user_version").fetchone()[0]
    out(
        f"   tables the v1 reader never asks about: {[t for t in tables if t != 'links']}; nothing raises"
    )
    out(
        f"   user_version read by v1 reader = {version} (it knows 1): the only signal it is short, if it checks"
    )
    try:
        old.execute("INSERT INTO links VALUES ('L3', 'lead-b', 'lead-c')")
        old.commit()
        out("   v1 writer, positional INSERT: worked")
    except sqlite3.OperationalError as exc:
        old.rollback()
        out(f"   v1 writer, positional INSERT: OperationalError: {exc}")
    old.execute("INSERT INTO links (id, a, b) VALUES ('L4', 'lead-b', 'lead-c')")
    old.commit()
    out(
        f"   v1 writer, named-column INSERT: worked; revoked_at stored as {old.execute('SELECT revoked_at FROM links WHERE id = ?', ('L4',)).fetchone()[0]!r}"
    )
    old.close()
    return path


# --------------------------------------------------------------------------- #
# 6. Inspectability
# --------------------------------------------------------------------------- #


def section_inspect(work: Path, schema_db: Path) -> None:
    out()
    out("== 6. Inspectability")
    out(f"sqlite3 CLI on PATH: {shutil.which('sqlite3')}")
    out(
        f"`python -m sqlite3` (sqlite3.__main__): {importlib.util.find_spec('sqlite3.__main__') is not None}"
    )
    conn = sqlite3.connect(str(schema_db))
    dump = list(conn.iterdump())
    conn.close()
    out(f"Connection.iterdump() of the schema-growth database: {len(dump)} statements, first five:")
    for line in dump[:5]:
        out(f"    {line}")

    path = work / "walcopy.db"
    c = sqlite3.connect(str(path), isolation_level=None)
    c.execute("PRAGMA journal_mode=wal")
    c.execute("PRAGMA wal_autocheckpoint=0")
    c.execute("CREATE TABLE t (x INTEGER)")
    for i in range(100):
        c.execute("INSERT INTO t VALUES (?)", (i,))
    copy = work / "walcopy-copy.db"
    shutil.copyfile(path, copy)
    seen = sqlite3.connect(str(copy))
    try:
        n = seen.execute("SELECT count(*) FROM t").fetchone()[0]
        copied = f"{n} rows"
    except sqlite3.OperationalError as exc:
        copied = f"OperationalError: {exc}"
    seen.close()
    backup = work / "walcopy-backup.db"
    dest = sqlite3.connect(str(backup))
    c.backup(dest)
    n_backup = dest.execute("SELECT count(*) FROM t").fetchone()[0]
    dest.close()
    c.close()
    out(f"WAL database, 100 committed rows, no checkpoint yet, writer still open:")
    out(f"   copy of the .db file alone sees: {copied}")
    out(f"   Connection.backup() copy sees: {n_backup} rows")


# --------------------------------------------------------------------------- #
# Q4 additions: the skeptic's needs-a-run list (S2-sqlite-skeptic, items A-J).
# Run with --q4; the Q2 sections above are not re-run then. Items A and H are
# shell checks of the installed library and venv and are recorded in
# q4-comparison.md, not here. Item I's runtime half needs Python 3.12+ and
# only its mypy half is here.
# --------------------------------------------------------------------------- #


def worker_try_write(a: argparse.Namespace) -> None:
    """
    One attempt at BEGIN IMMEDIATE with no busy wait. Prints "granted" and
    commits one row, or prints "refused: <error>".
    """
    conn = sqlite3.connect(a.db, timeout=0, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        print(f"refused: {exc}", flush=True)
        conn.close()
        return
    conn.execute("INSERT INTO t VALUES (?)", ("child",))
    conn.execute("COMMIT")
    print("granted", flush=True)
    conn.close()


def _child_write(db: Path) -> str:
    proc = subprocess.run(
        [PY, str(HERE), "--worker", "try-write", "--db", str(db)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return (proc.stdout + proc.stderr).strip()


def section_q4_close(work: Path) -> None:
    """
    B (S2 finding 5): Q3's close() probe, repeated in WAL mode with the raw
    open/read/close aimed at the .db, the -wal and the -shm in turn. The holder
    is this process, with BEGIN IMMEDIATE open and one row pending; the second
    writer is a separate process. If the second writer is granted, the holder's
    commit and an integrity_check follow, to see what the overlap did.
    """
    out()
    out("== Q4-B. close() of a raw descriptor in the holder process, by file and journal mode")
    for journal, target in (
        ("delete", ""),
        ("wal", ""),
        ("wal", "-wal"),
        ("wal", "-shm"),
    ):
        db = work / f"close-{journal}{target or '-db'}.db"
        setup = sqlite3.connect(str(db), isolation_level=None)
        setup.execute(f"PRAGMA journal_mode={journal}")
        setup.execute("CREATE TABLE t (who TEXT)")
        setup.execute("INSERT INTO t VALUES ('setup')")
        setup.close()
        holder = sqlite3.connect(str(db), timeout=0, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO t VALUES ('holder')")
        before = _child_write(db)
        raw = Path(str(db) + target)
        exists = raw.exists()
        if exists:
            fd = os.open(raw, os.O_RDONLY)
            os.read(fd, 16)
            os.close(fd)
        after = _child_write(db)
        try:
            holder.execute("COMMIT")
            held = "holder COMMIT ok"
        except sqlite3.OperationalError as exc:
            held = f"holder COMMIT failed: {exc}"
            with contextlib.suppress(sqlite3.Error):
                holder.execute("ROLLBACK")
        holder.close()
        check = sqlite3.connect(str(db))
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        rows = [r[0] for r in check.execute("SELECT who FROM t ORDER BY rowid")]
        check.close()
        name = "db" if not target else target.lstrip("-")
        out(f"-- journal={journal}, raw open/read/close on the {name} file (present: {exists})")
        out(f"   second process before: {before}")
        out(f"   second process after:  {after}")
        out(f"   {held}; integrity_check {integrity}; rows {rows}")


def section_q4_checkpoint(work: Path, iterations: int, k: int, seed: int) -> None:
    """
    C (S2 finding 11): Q2 section 3's WAL variant, with checkpoints under the
    kills. One variant sets wal_autocheckpoint=1 as the skeptic asked; one runs
    an explicit wal_checkpoint(FULL) after every group, bracketed in the log so
    the kills inside it are counted.
    """
    out()
    out("== Q4-C. SIGKILL with checkpoints under the kill (WAL)")
    section_crash(
        work,
        iterations,
        k,
        seed,
        1.0,
        [
            ("sqlite", "wal", "IMMEDIATE", ("--autocheckpoint", "1")),
            ("sqlite", "wal", "IMMEDIATE", ("--autocheckpoint", "0", "--checkpoint", "FULL")),
        ],
    )


def section_q4_threads(work: Path, n: int, seconds: float) -> None:
    """
    D (S2 findings 12, 9d): one process. Two threads, each with its own
    connection, doing section 2's read-modify-write under BEGIN IMMEDIATE. Then
    one writer thread writing k-row groups and one reader thread reading the
    whole table, counting any partial group the reader sees.
    """
    import threading

    out()
    out("== Q4-D. One process, several connections on threads")
    for journal in ("wal", "delete"):
        db = work / f"threads-{journal}.db"
        setup = sqlite3.connect(str(db), isolation_level=None)
        setup.execute(f"PRAGMA journal_mode={journal}")
        setup.execute("CREATE TABLE rows (wid INTEGER, seq INTEGER)")
        setup.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, v INTEGER)")
        setup.execute("INSERT INTO counter VALUES (1, 0)")
        setup.close()
        results: dict[int, tuple[int, dict[str, int], list[float]]] = {}
        barrier = threading.Barrier(2)

        def writer(wid: int) -> None:
            conn = sqlite3.connect(str(db), timeout=5.0, isolation_level=None)
            acked = 0
            errors: dict[str, int] = {}
            waits: list[float] = []
            barrier.wait()
            for seq in range(n):
                t0 = time.perf_counter()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    (value,) = conn.execute("SELECT v FROM counter WHERE id = 1").fetchone()
                    conn.execute("INSERT INTO rows (wid, seq) VALUES (?, ?)", (wid, seq))
                    conn.execute("UPDATE counter SET v = ? WHERE id = 1", (value + 1,))
                    conn.execute("COMMIT")
                    acked += 1
                    waits.append(time.perf_counter() - t0)
                except sqlite3.OperationalError as exc:
                    errors[str(exc)] = errors.get(str(exc), 0) + 1
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
            conn.close()
            results[wid] = (acked, errors, waits)

        threads = [threading.Thread(target=writer, args=(w,)) for w in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check = sqlite3.connect(str(db))
        rows = check.execute("SELECT count(*) FROM rows").fetchone()[0]
        counter = check.execute("SELECT v FROM counter").fetchone()[0]
        check.close()
        acked = sum(r[0] for r in results.values())
        errors: dict[str, int] = {}
        waits: list[float] = []
        for _, errs, ws in results.values():
            for key, v in errs.items():
                errors[key] = errors.get(key, 0) + v
            waits.extend(ws)
        out(f"-- journal={journal:6} two writer threads, BEGIN IMMEDIATE, busy_timeout=5000ms")
        out(
            f"   acked {acked}/{2 * n}; lost writes {acked - rows}; lost updates {rows - counter};"
            f" errors {errors if errors else 'none'}; wait ms p50 {pct(waits, 0.5):.2f}"
            f" p99 {pct(waits, 0.99):.2f} max {pct(waits, 1.0):.2f}"
        )

        db2 = work / f"threads-rw-{journal}.db"
        setup = sqlite3.connect(str(db2), isolation_level=None)
        setup.execute(f"PRAGMA journal_mode={journal}")
        setup.execute("CREATE TABLE rows (g INTEGER, j INTEGER)")
        setup.close()
        stop = threading.Event()
        groups = [0]
        reads = [0, 0, 0]  # reads, reads that saw a partial group, read errors
        k = 20

        def group_writer() -> None:
            conn = sqlite3.connect(str(db2), timeout=5.0, isolation_level=None)
            g = 0
            while not stop.is_set():
                conn.execute("BEGIN IMMEDIATE")
                for j in range(k):
                    conn.execute("INSERT INTO rows VALUES (?, ?)", (g, j))
                    if j % 5 == 0:
                        time.sleep(0.0005)
                conn.execute("COMMIT")
                g += 1
            groups[0] = g
            conn.close()

        def whole_reader() -> None:
            conn = sqlite3.connect(str(db2), timeout=5.0, isolation_level=None)
            while not stop.is_set():
                try:
                    counts = conn.execute("SELECT g, count(*) FROM rows GROUP BY g").fetchall()
                except sqlite3.OperationalError:
                    reads[2] += 1
                    continue
                reads[0] += 1
                if any(c != k for _, c in counts):
                    reads[1] += 1
            conn.close()

        pair = [threading.Thread(target=group_writer), threading.Thread(target=whole_reader)]
        for t in pair:
            t.start()
        time.sleep(seconds)
        stop.set()
        for t in pair:
            t.join()
        out(
            f"-- journal={journal:6} writer thread ({k}-row groups) + whole-table reader thread, {seconds:.0f}s"
        )
        out(
            f"   groups committed {groups[0]}; reads {reads[0]}; reads showing a partial group {reads[1]};"
            f" reads that raised {reads[2]}"
        )


def section_q4_think(work: Path, n: int, think_ms: float) -> None:
    """
    E (S2 finding 11): section 2's two writer processes with think time
    between transactions, in WAL and rollback mode.
    """
    out()
    out(f"== Q4-E. Two writer processes with {think_ms:.0f} ms think time between transactions")
    for journal in ("wal", "delete"):
        db = work / f"think-{journal}.db"
        setup = sqlite3.connect(str(db), isolation_level=None)
        setup.execute(f"PRAGMA journal_mode={journal}")
        setup.execute("CREATE TABLE rows (wid INTEGER, seq INTEGER)")
        setup.execute("CREATE TABLE counter (id INTEGER PRIMARY KEY, v INTEGER)")
        setup.execute("INSERT INTO counter VALUES (1, 0)")
        setup.close()
        start_at = time.time() + 1.0
        procs = []
        for wid in (1, 2):
            result = work / f"think-{journal}-{wid}.json"
            procs.append(
                (
                    result,
                    subprocess.Popen(
                        [
                            PY,
                            str(HERE),
                            "--worker",
                            "writer",
                            "--db",
                            str(db),
                            "--begin",
                            "IMMEDIATE",
                            "--busy-ms",
                            "5000",
                            "--n",
                            str(n),
                            "--wid",
                            str(wid),
                            "--start-at",
                            repr(start_at),
                            "--think-ms",
                            str(think_ms),
                            "--result",
                            str(result),
                        ]
                    ),
                )
            )
        for _, proc in procs:
            proc.wait()
        results = [json.loads(r.read_text()) for r, _ in procs]
        check = sqlite3.connect(str(db))
        rows = check.execute("SELECT count(*) FROM rows").fetchone()[0]
        counter = check.execute("SELECT v FROM counter").fetchone()[0]
        check.close()
        acked = sum(r["acked"] for r in results)
        errors: dict[str, int] = {}
        for r in results:
            for key, v in r["errors"].items():
                errors[key] = errors.get(key, 0) + v
        waits = [w for r in results for w in r["waits"]]
        begins = [w for r in results for w in r["begins"]]
        out(f"-- journal={journal:6} BEGIN IMMEDIATE busy_timeout=5000ms think={think_ms:.0f}ms")
        out(
            f"   acked {acked}/{2 * n}; lost writes {acked - rows}; lost updates {rows - counter};"
            f" errors {errors if errors else 'none'}"
        )
        out(
            f"   commit latency ms p50 {pct(waits, 0.5):.2f} p99 {pct(waits, 0.99):.2f} max {pct(waits, 1.0):.2f};"
            f" BEGIN wait ms p50 {pct(begins, 0.5):.2f} p99 {pct(begins, 0.99):.2f} max {pct(begins, 1.0):.2f}"
        )


def worker_wal_writer(a: argparse.Namespace) -> None:
    """
    Opens a WAL database with the default autocheckpoint, commits n one-row
    transactions, prints "ready", then waits for a line on stdin: "close"
    closes the connection cleanly and exits. A SIGKILL may come instead.
    """
    conn = sqlite3.connect(a.db, isolation_level=None)
    for i in range(a.n):
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO t VALUES (?)", (i,))
        conn.execute("COMMIT")
    print("ready", flush=True)
    line = sys.stdin.readline().strip()
    if line == "close":
        conn.close()
        print("closed", flush=True)


def _rows_in_copy(src: Path, dest_dir: Path, with_wal: bool) -> str:
    dest_dir.mkdir()
    copy = dest_dir / src.name
    shutil.copyfile(src, copy)
    wal = Path(str(src) + "-wal")
    if with_wal and wal.exists():
        shutil.copyfile(wal, Path(str(copy) + "-wal"))
    conn = sqlite3.connect(str(copy))
    try:
        result = f"{conn.execute('SELECT count(*) FROM t').fetchone()[0]} rows"
    except sqlite3.OperationalError as exc:
        result = f"OperationalError: {exc}"
    conn.close()
    return result


def section_q4_backup(work: Path, n: int) -> None:
    """
    F (S2 finding 8b): copying the .db file alone, with the default
    autocheckpoint, (i) while the writer is open, (ii) after its clean close,
    (iii) after a SIGKILL. The schema is made and the setup connection closed
    first, as an application's earlier run would have.
    """
    out()
    out(f"== Q4-F. Copying a WAL database by hand, default autocheckpoint, {n} one-row commits")
    for case in ("open", "clean close", "SIGKILL"):
        db = work / f"backup-{case.replace(' ', '-')}.db"
        setup = sqlite3.connect(str(db), isolation_level=None)
        setup.execute("PRAGMA journal_mode=wal")
        setup.execute("CREATE TABLE t (x INTEGER)")
        setup.close()
        proc = subprocess.Popen(
            [PY, str(HERE), "--worker", "wal-writer", "--db", str(db), "--n", str(n)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert proc.stdout is not None and proc.stdin is not None
        assert proc.stdout.readline().strip() == "ready"
        if case == "clean close":
            proc.stdin.write("close\n")
            proc.stdin.flush()
            proc.stdout.readline()
            proc.wait()
        elif case == "SIGKILL":
            proc.send_signal(signal.SIGKILL)
            proc.wait()
        wal = Path(str(db) + "-wal")
        wal_size = wal.stat().st_size if wal.exists() else None
        alone = _rows_in_copy(db, work / f"copy-alone-{case.replace(' ', '-')}", False)
        both = _rows_in_copy(db, work / f"copy-both-{case.replace(' ', '-')}", True)
        if case == "open":
            proc.stdin.write("close\n")
            proc.stdin.flush()
            proc.stdout.readline()
            proc.wait()
        out(
            f"-- writer {case}: -wal {'absent' if wal_size is None else f'{wal_size} bytes'};"
            f" copy of .db alone sees {alone}; .db + -wal sees {both}"
        )


def worker_cas(a: argparse.Namespace) -> None:
    """
    G (S2 finding 3): an expected-version append, the store's view refreshed
    only when an append is refused as stale. --mode files links to exactly one
    past the last ordinal seen and takes FileExistsError as stale (no moving
    up); --mode sqlite opens BEGIN IMMEDIATE, compares max(seq) with the last
    seen, and inserts seq+1 or rolls back as stale.
    """
    won = 0
    stale = 0
    errors = 0
    while time.time() < a.start_at:
        time.sleep(0.001)
    if a.mode == "files":
        directory = Path(a.dir)
        present = _ordinals(os.listdir(directory))
        seen = present[-1] if present else -1
        for _ in range(a.n):
            time.sleep(a.think_ms / 1000.0)
            fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=".cas-", suffix=".tmp")
            with os.fdopen(fd, "wb") as handle:
                handle.write(f"{a.wid}\n".encode())
            try:
                os.link(tmp, directory / f"{seen + 1:08d}")
                won += 1
                seen += 1
            except FileExistsError:
                stale += 1
                present = _ordinals(os.listdir(directory))
                seen = present[-1] if present else -1
            finally:
                os.unlink(tmp)
    else:
        conn = sqlite3.connect(a.db, timeout=5.0, isolation_level=None)
        seen = conn.execute("SELECT coalesce(max(seq), -1) FROM log").fetchone()[0]
        for _ in range(a.n):
            time.sleep(a.think_ms / 1000.0)
            try:
                conn.execute("BEGIN IMMEDIATE")
                (latest,) = conn.execute("SELECT coalesce(max(seq), -1) FROM log").fetchone()
                if latest != seen:
                    conn.execute("ROLLBACK")
                    stale += 1
                    seen = latest
                    continue
                conn.execute("INSERT INTO log (seq, wid) VALUES (?, ?)", (seen + 1, a.wid))
                conn.execute("COMMIT")
                won += 1
                seen += 1
            except sqlite3.OperationalError:
                errors += 1
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
        conn.close()
    Path(a.result).write_text(json.dumps({"won": won, "stale": stale, "errors": errors}))


def section_q4_cas(work: Path, n: int, think_ms: float = 0.0) -> None:
    """
    G: two processes running worker_cas against one log, files and SQLite.
    Afterwards: the records present, duplicates, and gaps in the sequence.
    """
    out()
    out(
        f"== Q4-G. Expected-version append from two processes, {n} attempts each,"
        f" {think_ms:g} ms between attempts"
    )
    for mode in ("files", "sqlite"):
        directory = work / f"cas-files-{think_ms:g}"
        db = work / f"cas-{think_ms:g}.db"
        if mode == "files":
            directory.mkdir()
        else:
            setup = sqlite3.connect(str(db), isolation_level=None)
            setup.execute("PRAGMA journal_mode=wal")
            setup.execute("CREATE TABLE log (seq INTEGER, wid INTEGER)")
            setup.close()
        start_at = time.time() + 1.0
        procs = []
        for wid in (1, 2):
            result = work / f"cas-{mode}-{think_ms:g}-{wid}.json"
            procs.append(
                (
                    result,
                    subprocess.Popen(
                        [
                            PY,
                            str(HERE),
                            "--worker",
                            "cas",
                            "--mode",
                            mode,
                            "--dir",
                            str(directory),
                            "--db",
                            str(db),
                            "--n",
                            str(n),
                            "--wid",
                            str(wid),
                            "--start-at",
                            repr(start_at),
                            "--think-ms",
                            str(think_ms),
                            "--result",
                            str(result),
                        ]
                    ),
                )
            )
        for _, proc in procs:
            proc.wait()
        results = [json.loads(r.read_text()) for r, _ in procs]
        if mode == "files":
            seqs = _ordinals(os.listdir(directory))
            leftovers = sum(1 for p in directory.iterdir() if p.name.endswith(".tmp"))
        else:
            check = sqlite3.connect(str(db))
            seqs = sorted(r[0] for r in check.execute("SELECT seq FROM log"))
            check.close()
            leftovers = 0
        won = sum(r["won"] for r in results)
        dupes = len(seqs) - len(set(seqs))
        gaps = (seqs[-1] + 1 - len(set(seqs))) if seqs else 0
        out(
            f"-- {mode}: won {won} (per writer {[r['won'] for r in results]}),"
            f" stale {sum(r['stale'] for r in results)}, errors {sum(r['errors'] for r in results)};"
            f" records {len(seqs)}, duplicate sequence numbers {dupes}, gaps {gaps}, temp files left {leftovers}"
        )


_BRANCHED = """\
import sqlite3
import sys


def connect(path: str) -> sqlite3.Connection:
    if sys.version_info >= (3, 12):
{branch}
    return sqlite3.connect(path, isolation_level=None)


conn = connect(":memory:")
conn.execute("BEGIN IMMEDIATE")
conn.execute("COMMIT")
conn.close()
"""

# Three ways to keep floor-style code (isolation_level=None plus an explicit
# BEGIN) working once Python's autocommit default changes.
MYPY_BRANCHED_CONNECTS = {
    "connect(autocommit=LEGACY_TRANSACTION_CONTROL)": _BRANCHED.format(
        branch="        return sqlite3.connect(\n"
        "            path, autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL, isolation_level=None\n"
        "        )"
    ),
    "connect(autocommit=True)": _BRANCHED.format(
        branch="        return sqlite3.connect(path, autocommit=True)"
    ),
    "connect() then .autocommit = LEGACY_TRANSACTION_CONTROL": _BRANCHED.format(
        branch="        conn = sqlite3.connect(path, isolation_level=None)\n"
        "        conn.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL\n"
        "        return conn"
    ),
}


def section_q4_mypy(work: Path, mypy: str | None) -> None:
    """
    I (S2 finding 7), the half that runs here: a connect branched on
    sys.version_info, checked by mypy --strict at 3.11, 3.12 and 3.14, and run
    on this interpreter. The runtime behaviour of a nested BEGIN under
    autocommit=False needs a 3.12+ interpreter, which this box does not have.
    """
    out()
    out(
        "== Q4-I. A version-branched connect: mypy --strict per target, and a run on this interpreter"
    )
    for label, text in MYPY_BRANCHED_CONNECTS.items():
        out(f"-- {label}")
        snippet = work / "branched_connect.py"
        snippet.write_text(text)
        if mypy is None:
            out("   mypy: not given (--mypy PATH), not checked")
        else:
            for version in ("3.11", "3.12", "3.14"):
                proc = subprocess.run(
                    [
                        mypy,
                        "--python-version",
                        version,
                        "--strict",
                        "--cache-dir",
                        str(work / "mypy-cache"),
                        "--no-incremental",
                        str(snippet),
                    ],
                    capture_output=True,
                    text=True,
                    cwd=str(work),
                )
                errors = [
                    ln.split(": error: ", 1)[1]
                    for ln in (proc.stdout + proc.stderr).splitlines()
                    if ": error: " in ln
                ]
                out(f"   mypy --python-version {version} --strict: exit {proc.returncode} {errors}")
        proc = subprocess.run([PY, str(snippet)], capture_output=True, text=True)
        out(
            f"   run on {sys.version.split()[0]}: exit {proc.returncode}"
            + (f" {proc.stderr.strip().splitlines()[-1]}" if proc.stderr.strip() else "")
        )


def section_q4_placement(work: Path, tree: str | None) -> None:
    """
    J (S2 finding 9): what boardsync.migrate does with a SQLite file and its
    sidecars under the legacy boards/ root, and whether sessionfile.listing
    takes a file, or a directory, under sessions/ as a session. Imports this
    tree's pptmstr with bytecode writing off, and passes both roots explicitly
    so nothing under the real configuration directory is touched.
    """
    out()
    out("== Q4-J. Placement: a channels.db under boards/ and under sessions/")
    if tree is None:
        out("--tree not given, not checked")
        return
    sys.dont_write_bytecode = True
    sys.path.insert(0, tree)
    from pptmstr import boardsync, sessionfile

    legacy = work / "placement" / "boards"
    sessions = work / "placement" / "sessions"
    legacy.mkdir(parents=True)
    sessions.mkdir()
    for name in ("channels.db", "channels.db-wal", "channels.db-shm"):
        (legacy / name).write_bytes(b"")
    migration = boardsync.migrate(root=sessions, legacy=legacy)
    for entry in migration.entries:
        out(f"   migrate: {entry!r}")
        if isinstance(entry, boardsync.Left):
            out(f"      reason: {entry.reason}; twice={entry.twice}")
    out(
        f"   boards/ after: {sorted(os.listdir(legacy))}; sessions/ after: {sorted(os.listdir(sessions))}"
    )
    (sessions / "channels.db").write_bytes(b"")
    listing = sessionfile.listing(sessions)
    out(
        f"   sessions/ holding a file channels.db: listing ids {[sid for sid, _ in listing.sessions]}"
    )
    (sessions / "channels").mkdir()
    listing = sessionfile.listing(sessions)
    out(
        f"   sessions/ also holding a directory channels/: listing"
        f" {[(sid, type(found).__name__) for sid, found in listing.sessions]}"
    )


def run_q4(a: argparse.Namespace, work: Path) -> None:
    section_q4_close(work)
    section_q4_checkpoint(work, a.iterations, a.k, a.seed)
    section_q4_threads(work, a.n, 20.0)
    section_q4_think(work, a.n, 100.0)
    section_q4_backup(work, 100)
    section_q4_cas(work, a.n)
    section_q4_cas(work, a.n, 1.0)
    section_q4_mypy(work, a.mypy)
    section_q4_placement(work, a.tree)


# --------------------------------------------------------------------------- #


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker")
    parser.add_argument("--db")
    parser.add_argument("--dir")
    parser.add_argument("--log")
    parser.add_argument("--begin", default="IMMEDIATE")
    parser.add_argument("--busy-ms", type=int, default=5000)
    parser.add_argument("--n", type=int, default=500)
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument("--wid", type=int, default=0)
    parser.add_argument("--start-at", type=float, default=0.0)
    parser.add_argument("--result")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--row-sleep-ms", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=20261017)
    parser.add_argument("--mypy")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--think-ms", type=float, default=0.0)
    parser.add_argument("--autocheckpoint", type=int)
    parser.add_argument("--checkpoint")
    parser.add_argument("--mode")
    parser.add_argument("--q4", action="store_true", help="run only the Q4 additions")
    parser.add_argument("--tree", help="a pptmstr tree to import for Q4-J")
    a = parser.parse_args()

    if a.worker == "writer":
        return worker_writer(a)
    if a.worker == "crash-sqlite":
        return worker_crash_sqlite(a)
    if a.worker == "crash-files":
        return worker_crash_files(a)
    if a.worker == "try-write":
        return worker_try_write(a)
    if a.worker == "wal-writer":
        return worker_wal_writer(a)
    if a.worker == "cas":
        return worker_cas(a)

    tmpdir = os.environ.get("TMPDIR")
    if not tmpdir:
        sys.exit("TMPDIR is not set; this probe writes only under $TMPDIR")
    work = Path(tempfile.mkdtemp(prefix="q2-sqlite-", dir=tmpdir))
    out(f"work directory    {work}")
    out(f"filesystem of the work directory: {fstype(work)}")
    out(f"filesystem of ~/.config/pptmstr:  {fstype(Path.home() / '.config' / 'pptmstr')}")
    out(f"filesystem of the probe's tree:   {fstype(HERE.parent)}")
    started = time.perf_counter()
    try:
        if a.q4:
            out(f"python {sys.version.split()[0]}, sqlite {sqlite3.sqlite_version}")
            run_q4(a, work)
            return
        section_versions(work, a.mypy)
        section_writers(work, a.n)
        section_crash(work, a.iterations, a.k, a.seed, a.row_sleep_ms)
        section_quirks(work)
        schema_db = section_schema(work)
        section_inspect(work, schema_db)
    finally:
        out()
        out(f"elapsed {time.perf_counter() - started:.1f}s")
        if not a.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
