"""
M3: run S5's needs-a-run items A, C, D and E (and the P-side mirrors), so that
every H+-only and P-only silent site in the recount is confirmed or refuted by a
run rather than by reading.

Usage (from anywhere; the interpreter is the project venv):

    /home/wreel/Source/pptmstr/.venv/bin/python -B m3_checks.py WORKTREE

Stdlib only in this process. It never imports pptmstr itself. Every probe of
pptmstr runs in a child interpreter with ``PYTHONPATH`` set to the tree under test
and prints ``pptmstr.__file__`` first, so the import path of every run is in the
output. mypy ignores ``PYTHONPATH``, so it runs with ``cwd`` and ``MYPYPATH`` both
set to the scratch tree and ``--config-file pyproject.toml``. pytest runs as
``python -m pytest`` with ``cwd`` set to the scratch tree and no ``PYTHONPATH``
(``-m`` puts the cwd first on ``sys.path``; the import line printed beside each
run shows which tree that resolved to).

Scratch trees, probes, the mypy cache and the encoded records go under
``$TMPDIR/m3``. Nothing is written in the worktree, and bytecode is off for every
child, so nothing is left in planning/.

Each scratch tree is a copy of ``pptmstr/``, ``tests/`` and ``pyproject.toml``
with some of the patches in ``PATCHES`` applied. A patch is a literal text
replacement that must match exactly once, so a tree that moved under the script
stops it rather than producing a variant nobody intended.

B (``bus._withheld``) is conditional on ``send_to_peer`` accepting ``incident``,
which nobody has decided, and is deliberately not run.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

# -- patches: (file, old, new), each old matching exactly once ---------------------

PATCHES: dict[str, list[tuple[str, str, str]]] = {
    # Concern gains a channel that defaults to None: H+ as M2 §(d) specified it.
    "concern_channel": [
        (
            "pptmstr/model.py",
            "    incident: bool = False\n\n\nclass TaskState(enum.Enum):",
            "    incident: bool = False\n    channel: str | None = None\n\n\n"
            "class TaskState(enum.Enum):",
        )
    ],
    # The alternative: channel is keyword-only with no default, so every
    # construction has to say it.
    "concern_channel_required": [
        (
            "pptmstr/model.py",
            "    incident: bool = False\n\n\nclass TaskState(enum.Enum):",
            "    incident: bool = False\n    channel: str | None = field(kw_only=True)\n\n\n"
            "class TaskState(enum.Enum):",
        )
    ],
    # H+'s inbox_of: excludes channel mail.
    "inbox_partition": [
        (
            "pptmstr/model.py",
            "if c.recipient == node and c.state is ConcernState.POSTED",
            "if c.recipient == node and c.state is ConcernState.POSTED and c.channel is None",
        )
    ],
    "restored_concern_channel": [
        (
            "pptmstr/boardlog.py",
            "    incident: bool = False\n    # As on ``RestoredTask``",
            "    incident: bool = False\n    channel: str | None = None\n"
            "    # As on ``RestoredTask``",
        )
    ],
    # store._restored_concern remembers to copy the field.
    "store_copies": [
        (
            "pptmstr/store.py",
            "        incident=logged.incident,\n",
            "        incident=logged.incident,\n        channel=logged.channel,\n",
        )
    ],
    # The widened-Posted mistake: a field on concern-posted instead of a new kind.
    "widened_posted": [
        (
            "pptmstr/boardlog.py",
            "    recipient_id: tuple[str, ...] = ()\n",
            "    recipient_id: tuple[str, ...] = ()\n    channel: str | None = None\n",
        ),
        (
            "pptmstr/boardlog.py",
            "                    incident=record.incident,\n",
            "                    incident=record.incident,\n"
            "                    channel=record.channel,\n",
        ),
        (
            "pptmstr/boardfacts.py",
            "            incident=concern.incident,\n",
            "            incident=concern.incident,\n            channel=concern.channel,\n",
        ),
    ],
    # H+ as intended: a peer-posted kind, chosen in _filed by channel. A subclass of
    # Posted is the scratch shortcut; it inherits every arm that matches Posted().
    "peer_kind": [
        (
            "pptmstr/boardlog.py",
            "@dataclass(frozen=True, slots=True)\nclass Edited:",
            "@dataclass(frozen=True, slots=True)\nclass PeerPosted(Posted):\n"
            '    KIND: ClassVar[str] = "peer-posted"\n\n'
            "    channel: str | None = None\n\n\n"
            "@dataclass(frozen=True, slots=True)\nclass Edited:",
        ),
        ("pptmstr/boardlog.py", "    | Posted\n", "    | Posted\n    | PeerPosted\n"),
        (
            "pptmstr/boardlog.py",
            "                    incident=record.incident,\n",
            "                    incident=record.incident,\n"
            "                    channel=record.channel if isinstance(record, PeerPosted) "
            "else None,\n",
        ),
        (
            "pptmstr/boardfacts.py",
            "    if concern.edited:\n",
            "    if concern.channel is not None:\n"
            "        first = records[0]\n"
            "        assert isinstance(first, boardlog.Posted)\n"
            "        records[0] = boardlog.PeerPosted(\n"
            "            **{f.name: getattr(first, f.name) for f in dataclasses.fields(first)},\n"
            "            channel=concern.channel,\n"
            "        )\n"
            "    if concern.edited:\n",
        ),
    ],
    # The store-reaching round trip's generator sets the new field.
    "generator_sets_channel": [
        (
            "tests/test_boardfacts.py",
            "        incident=r.random() < 0.3,\n",
            '        incident=r.random() < 0.3,\n        channel=r.choice((None, None, "x")),\n',
        )
    ],
    # E: any call into store._restored_concern raises.
    "store_restore_raises": [
        (
            "pptmstr/store.py",
            "    sender = node_of(logged.sender)\n    if sender[0] != session_id:\n"
            "        return Unplaced.NOT_THIS_SESSIONS\n    return Concern(",
            '    raise AssertionError("store._restored_concern reached")\n'
            "    sender = node_of(logged.sender)\n    if sender[0] != session_id:\n"
            "        return Unplaced.NOT_THIS_SESSIONS\n    return Concern(",
        )
    ],
    # P2: peers in a collection of their own, on the restored board and the snapshot.
    "p2_maps": [
        (
            "pptmstr/boardlog.py",
            "    stopped: tuple[RecordingStopped, ...] = ()\n",
            "    stopped: tuple[RecordingStopped, ...] = ()\n"
            "    peers: tuple[RestoredConcern, ...] = ()\n",
        ),
        (
            "pptmstr/model.py",
            "    command_since: Mapping[CommandId, float] = field(default_factory=_empty_map)\n",
            "    command_since: Mapping[CommandId, float] = field(default_factory=_empty_map)\n"
            "    peers: Mapping[ConcernId, Concern] = field(default_factory=_empty_map)\n",
        ),
    ],
}

H_PLUS = ["concern_channel", "inbox_partition", "restored_concern_channel"]

VARIANTS: dict[str, list[str]] = {
    "base": [],
    "a0_no_partition": ["concern_channel"],
    "a_hplus_no_copy": H_PLUS,
    "a_hplus_copy": [*H_PLUS, "store_copies"],
    "a_required": ["concern_channel_required", "inbox_partition", "restored_concern_channel"],
    "c_widened": [*H_PLUS, "store_copies", "widened_posted"],
    "c_kind": [*H_PLUS, "store_copies", "peer_kind"],
    "c_kind_no_copy": [*H_PLUS, "peer_kind"],
    "n1_kind_gen_no_copy": [*H_PLUS, "peer_kind", "generator_sets_channel"],
    "n1_kind_gen_copy": [*H_PLUS, "peer_kind", "generator_sets_channel", "store_copies"],
    "n1_kind_no_gen_no_copy": [*H_PLUS, "peer_kind"],
    "e_store_raises": ["store_restore_raises"],
    "p2": ["p2_maps"],
}

FIELD_WALKER = (
    "tests/test_boardlog.py::"
    "test_every_field_of_a_board_record_has_a_record_that_restores_it_or_is_left_out_by_name"
)
WHOLE_PATH = (
    "tests/test_boardfacts.py::"
    "test_a_log_restored_into_another_process_is_the_board_the_first_one_held"
)

# -- probes, each run in a child with PYTHONPATH at the tree under test ------------

PRELUDE = """
import json, sys
import pptmstr
print("import:", pptmstr.__file__)
from pptmstr import boardlog, boardfacts
from pptmstr.boardlog import Board, Clock, RestoredConcern
from pptmstr.intents import AgentSpawned, BoardRestored, ConcernPosted
from pptmstr.model import Concern, Snapshot
from pptmstr.store import _apply

A, B = ("A", None), ("B", None)
CLOCK = Clock(wall=100.0, monotonic=100.0)


def spawned():
    snap = Snapshot.empty()
    for node in (A, B):
        snap, _ = _apply(snap, AgentSpawned(node_id=node, parent=None, task="t",
                         model="m", started_at=0.0, agent_type=None, cwd="/w"), 0.0)
    return snap


def restored_into_empty(session_id, board):
    snap, _ = _apply(Snapshot.empty(), BoardRestored(session_id, board, CLOCK), 100.0)
    return snap
"""

PROBES: dict[str, str] = {
    # A: a peer RestoredConcern handed to the store.
    "restore": """
board = Board(concerns=(RestoredConcern(id="c-1", sender=A, recipient=B, subject="s",
              body="b", posted_at=1.0, channel="x"),))
snap = restored_into_empty("A", board)
report = snap.restores["A"]
c = snap.concerns["c-1"]
print("RESULT restored_channel", repr(c.channel))
print("RESULT in_inbox_of_B", c in snap.inbox_of(B))
print("RESULT report_unplaced", report.unplaced, "short", report.short)
""",
    # A0 / inbox_of: a live channel concern and B's read_inbox projection.
    "live_inbox": """
snap, _ = _apply(spawned(), ConcernPosted(A, Concern(id="c-1", sender=A, recipient=B,
                 subject="s", body="b", posted_at=1.0, channel="x")), 1.0)
print("RESULT in_inbox_of_B", snap.concerns["c-1"] in snap.inbox_of(B))
""",
    # C: what _filed writes, M2's round-trip pin, and the kind pin.
    "write": """
before = spawned()
concern = Concern(id="c-1", sender=A, recipient=B, subject="s", body="b",
                  posted_at=1.0, channel="x")
intent = ConcernPosted(A, concern)
after, _ = _apply(before, intent, 1.0)
facts = boardfacts.outcomes(before, intent, after, 1.0)
texts = [boardlog.encode(f.record) for f in facts]
with open(sys.argv[1], "w") as out:
    out.write("".join(texts))
kinds = [json.loads(t)["kind"] for t in texts]
print("RESULT kinds_written", kinds)
decoded = [boardlog.decode(t) for t in texts]
snap = restored_into_empty("A", boardlog.restore(decoded))
round_trip = snap.concerns["c-1"].channel == "x"
print("RESULT m2_round_trip_pin", "PASS" if round_trip else "FAIL")
kind_pin = "peer-posted" in kinds and "concern-posted" not in kinds
print("RESULT kind_pin", "PASS" if kind_pin else "FAIL")
""",
    # C: the unmodified worktree as the older build, reading what the new one wrote.
    "older_reads": """
with open(sys.argv[1]) as f:
    texts = [line + "\\n" for line in f.read().splitlines()]
decoded = [boardlog.decode(t) for t in texts]
print("RESULT older_decodes_as", [type(d).__name__ for d in decoded])
board = boardlog.restore(decoded)
print("RESULT older_board_short", board.short, "unrecognised",
      [u.kind for u in board.unrecognised])
snap = restored_into_empty("A", board)
mail = snap.concerns.get("c-1")
print("RESULT older_places_it", mail is not None)
print("RESULT older_in_inbox_of_B", mail is not None and mail in snap.inbox_of(B))
""",
    # P2's mirrors: a separate map that existing code never reads.
    "p2": """
from pptmstr import app, digest
peer = RestoredConcern(id="p-1", sender=A, recipient=B, subject="s", body="b",
                       posted_at=7.0, incident=True)
board = Board(peers=(peer,))
snap = restored_into_empty("A", board)
report = snap.restores["A"]
print("RESULT p2_placed", "p-1" in snap.concerns or "p-1" in snap.peers)
print("RESULT p2_report", "unplaced", report.unplaced, "short", report.short,
      "concerns", report.concerns)
print("RESULT p2_newest", app._newest(board), "vs concerns-field",
      app._newest(Board(concerns=(peer,))))
live = Concern(id="p-1", sender=A, recipient=B, subject="s", body="b", posted_at=7.0,
               incident=True)
import dataclasses
d_peer = digest.digest(dataclasses.replace(spawned(), peers={"p-1": live}), "A")
d_concern = digest.digest(dataclasses.replace(spawned(), concerns={"p-1": live}), "A")
print("RESULT p2_digest_incidents", len(d_peer.incidents) + len(d_peer.earlier_incidents),
      "vs concerns-map", len(d_concern.incidents) + len(d_concern.earlier_incidents))
""",
}

# -- machinery ---------------------------------------------------------------------


def child_env(pythonpath: Path | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "MYPYPATH")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    return env


def build(worktree: Path, scratch: Path, name: str) -> Path:
    tree = scratch / "trees" / name
    if tree.exists():
        shutil.rmtree(tree)
    ignore = shutil.ignore_patterns("__pycache__", ".mypy_cache", ".pytest_cache")
    for part in ("pptmstr", "tests"):
        shutil.copytree(worktree / part, tree / part, ignore=ignore)
    shutil.copy2(worktree / "pyproject.toml", tree / "pyproject.toml")
    for patch in VARIANTS[name]:
        for rel, old, new in PATCHES[patch]:
            path = tree / rel
            text = path.read_text()
            count = text.count(old)
            if count != 1:
                sys.exit(f"patch {patch} matches {count} times in {rel}; the tree moved")
            path.write_text(text.replace(old, new))
    return tree


def mypy(tree: Path, scratch: Path, name: str) -> str:
    env = child_env(None)
    env["MYPYPATH"] = str(tree)
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            "pyproject.toml",
            "--cache-dir",
            str(scratch / "mypy-cache" / name),
            "pptmstr",
        ],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
    )
    return run.stdout.strip() + run.stderr.strip()


def probe(tree: Path, scratch: Path, name: str, *args: str) -> str:
    probes = scratch / "probes"
    probes.mkdir(parents=True, exist_ok=True)
    script = probes / f"{name}.py"
    script.write_text(PRELUDE + textwrap.dedent(PROBES[name]))
    run = subprocess.run(
        [sys.executable, "-B", str(script), *args],
        cwd=probes,
        env=child_env(tree),
        capture_output=True,
        text=True,
    )
    return (run.stdout + run.stderr).strip()


def pytest(tree: Path, nodeid: str) -> str:
    where = subprocess.run(
        [sys.executable, "-B", "-c", "import pptmstr; print('import:', pptmstr.__file__)"],
        cwd=tree,
        env=child_env(None),
        capture_output=True,
        text=True,
    ).stdout.strip()
    run = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "-x",
            "--no-header",
            nodeid,
        ],
        cwd=tree,
        env=child_env(None),
        capture_output=True,
        text=True,
    )
    tail = [line for line in run.stdout.splitlines() if line.strip()][-1:]
    failed = [line for line in run.stdout.splitlines() if line.startswith("E ")][:3]
    return "\n".join([where, *failed, *tail])


def section(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 70 - len(title)))


def concern_constructions(worktree: Path) -> list[str]:
    """Every call of Concern(...) or RestoredConcern(...) in pptmstr/, with its function."""
    found = []
    for path in sorted((worktree / "pptmstr").rglob("*.py")):
        tree = ast.parse(path.read_text())
        owners: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call):
                        owners.setdefault(id(inner), node.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            callee = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None)
            )
            if callee in ("Concern", "RestoredConcern"):
                rel = path.relative_to(worktree)
                found.append(f"{rel}:{node.lineno} {callee}( in {owners.get(id(node), '<module>')}")
    return found


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    worktree = Path(sys.argv[1]).resolve()
    scratch = Path(os.environ.get("TMPDIR", "/tmp")) / "m3"
    scratch.mkdir(parents=True, exist_ok=True)
    print("interpreter:", sys.executable, sys.version.split()[0])
    print("worktree:", worktree)
    print("scratch:", scratch)

    trees = {name: build(worktree, scratch, name) for name in VARIANTS}

    section("mypy (MYPYPATH and cwd = the scratch tree, --config-file pyproject.toml)")
    for name in (
        "base",
        "a0_no_partition",
        "a_hplus_no_copy",
        "a_required",
        "c_widened",
        "c_kind",
        "p2",
    ):
        print(f"[{name}] {'+'.join(VARIANTS[name]) or 'unmodified'}")
        print(textwrap.indent(mypy(trees[name], scratch, name), "    "))

    section("A: inbox_of and store._restored_concern")
    for name in ("a0_no_partition", "a_hplus_no_copy"):
        print(f"[{name}] live_inbox")
        print(textwrap.indent(probe(trees[name], scratch, "live_inbox"), "    "))
    for name in ("a_hplus_no_copy", "a_hplus_copy"):
        print(f"[{name}] restore")
        print(textwrap.indent(probe(trees[name], scratch, "restore"), "    "))

    section("C: what _filed writes, and what each pin says")
    for name in ("c_widened", "c_kind", "c_kind_no_copy"):
        records = scratch / f"{name}.records"
        print(f"[{name}] write")
        print(textwrap.indent(probe(trees[name], scratch, "write", str(records)), "    "))
        print(f"[{name}] older build (unmodified worktree) reads it")
        print(textwrap.indent(probe(worktree, scratch, "older_reads", str(records)), "    "))

    section("D: Concern( and RestoredConcern( constructions in pptmstr/ (by AST)")
    for line in concern_constructions(worktree):
        print("   ", line)

    section("E: does the field walker reach store? does any round trip?")
    source = (worktree / "tests" / "test_boardlog.py").read_text()
    imported = sorted(
        {
            (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(ast.parse(source))
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in n.names
        }
    )
    print("    test_boardlog.py imports:", ", ".join(m for m in imported if "pptmstr" in m))
    # The import alone does not answer E: say where store is imported, then run the
    # walker with store._restored_concern raising.
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(n):
                if isinstance(inner, ast.ImportFrom) and inner.module == "pptmstr.store":
                    print(f"    pptmstr.store imported at :{inner.lineno} inside {n.name}")
    for name, nodeid in (
        ("e_store_raises", FIELD_WALKER),
        ("e_store_raises", WHOLE_PATH),
        ("a_hplus_no_copy", FIELD_WALKER),
        ("n1_kind_no_gen_no_copy", WHOLE_PATH),
        ("n1_kind_gen_no_copy", WHOLE_PATH),
        ("n1_kind_gen_copy", WHOLE_PATH),
    ):
        print(f"[{name}] {nodeid.split('::')[1][:60]}...")
        print(textwrap.indent(pytest(trees[name], nodeid), "    "))

    section("P2 mirrors: placement loop, _newest, digest incidents")
    print("[p2] p2")
    print(textwrap.indent(probe(trees["p2"], scratch, "p2"), "    "))


if __name__ == "__main__":
    main()
