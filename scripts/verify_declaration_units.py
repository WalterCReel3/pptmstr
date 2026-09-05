#!/usr/bin/env python3
"""
Are a declaration and a measured write ever in different units, and which way does it fail?

``ApprovedWrites.paths``' docstring states the invariant the whole divergence
measurement rests on: *"repository-relative and distinct... so it can be compared
against a declaration written in the same units."* But the path is produced by
``relative_write``, which strips ``AgentRecord.cwd`` -- and ``normalised_touches``
says explicitly that it *"does not resolve a path against a session's cwd"*, while
``bus.declare_task``'s schema string tells every lead to give paths relative to the
repository root. Those are the same units only when cwd IS the repository root, and
nothing establishes that: ``LaunchSpec.cwd`` defaults to ``"."`` and is otherwise
free text from an ImGui field.

The 2026-09-02 amendment to ``planning/2026-09-01`` exists because a units mismatch
would have made an observation period *"measure nothing but itself"*. That amendment
reconciled absolute-versus-relative. This asks whether root-versus-subdirectory was
left open one level up.

**Two directions, and they are not equally bad.**

  LOUD   A compliant agent reported as diverging. ``wrote_outside_declaration``
         lights on a task that did exactly what it declared. Wrong, visible, and it
         discredits the one line the operator is meant to trust.

  SILENT ``touches`` is also the input to ``store._auto_depends``, which its own
         docstring calls *"the entire mechanism keeping two agents out of one file,
         mechanical and unbypassable."* It compares normalised strings. Two tasks
         naming the same physical file in different units do not collide, no
         dependency edge is added, and both agents are handed the file at once. That
         is the failure the field exists to prevent, arriving through the field.

This is a pure-function probe: everything under test is total and does no IO, so the
answer is exact rather than observational. Nothing here launches a session and
nothing here writes to the tree.

Usage:  .venv/bin/python scripts/verify_declaration_units.py
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pptmstr import model  # noqa: E402
from pptmstr.store import _auto_depends  # noqa: E402

REPO = "/home/w/repo"
SUBDIR = "/home/w/repo/pptmstr"

# What bus.declare_task's schema string instructs a lead to write.
DECLARED = "pptmstr/store.py"
# The same physical file, as an absolute path and as the model would spell it
# relative to a session running in SUBDIR.
ABS_WRITE = "/home/w/repo/pptmstr/store.py"
REL_WRITE_FROM_SUBDIR = "store.py"


def _make_task(task_id: str, touches: tuple[str, ...]) -> model.Task:
    """
    Build a Task with only the fields this probe needs, whatever else it carries.

    Introspected rather than spelled out so the probe keeps working across the
    operator's in-flight edits to model.py rather than failing on a signature change
    and being mistaken for a finding.
    """
    # `declared_by` is not optional in practice even though the field is: _auto_depends
    # returns () immediately when it is None, before it compares a single path. Leaving
    # it unset makes the overlap check look like it found nothing, which is
    # indistinguishable from the defect under test.
    kwargs = {
        "id": task_id,
        "touches": model.normalised_touches(touches),
        "declared_by": ("probe-session", None),
    }
    for field in dataclasses.fields(model.Task):
        if field.name in kwargs:
            continue
        has_default = field.default is not dataclasses.MISSING
        has_factory = field.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        if not has_default and not has_factory:
            kwargs[field.name] = "probe" if field.type in (str, "str") else None
    return model.Task(**kwargs)  # type: ignore[arg-type]


def report_placement() -> bool:
    """
    Does an absolute write land in the declaration's units? Returns True if the
    subdirectory case misplaces it.
    """
    print("=== relative_write: does a write land in the units a declaration uses? ===")
    print(f"  declaration (per bus schema):  {DECLARED!r}")

    rows = [
        ("cwd == repo root  (control)", REPO, ABS_WRITE),
        ("cwd == subdirectory (suspect)", SUBDIR, ABS_WRITE),
        ("cwd == subdirectory, relative", SUBDIR, REL_WRITE_FROM_SUBDIR),
    ]
    misplaced = False
    for label, cwd, path in rows:
        placed = model.relative_write(path, cwd)
        agrees = placed == DECLARED
        if not agrees:
            misplaced = True
        print(f"  {label:<32} cwd={cwd}")
        print(f"      write {path!r} -> {placed!r}   matches declaration: {agrees}")
    return misplaced


def report_loud() -> bool:
    """
    Does a compliant agent get reported as diverging?
    """
    print("\n=== LOUD direction: wrote_outside_declaration on a compliant agent ===")
    fired = False
    for label, cwd in (("repo root (control)", REPO), ("subdirectory (suspect)", SUBDIR)):
        placed = model.relative_write(ABS_WRITE, cwd)
        task = _make_task("t-loud", (DECLARED,))
        task = dataclasses.replace(
            task, writes=model.ApprovedWrites(paths=(placed,) if placed else ())
        )
        outside = task.wrote_outside_declaration()
        if outside:
            fired = True
        print(f"  {label:<24} declared={DECLARED!r} recorded={placed!r}")
        print(f"      wrote_outside_declaration() -> {outside}")
    return fired


def report_silent() -> bool:
    """
    Do two tasks naming the same physical file in different units collide?

    ``_auto_depends`` is what keeps two agents out of one file. If it misses, both
    are handed the file and nothing says so.
    """
    print("\n=== SILENT direction: _auto_depends overlap across mismatched units ===")
    existing = _make_task("t-first", (DECLARED,))
    tasks = {existing.id: existing}

    same_units = _make_task("t-second", (DECLARED,))
    mixed_units = _make_task("t-second", (REL_WRITE_FROM_SUBDIR,))

    got_same = _auto_depends(tasks, same_units)
    got_mixed = _auto_depends(tasks, mixed_units)

    print(f"  existing task declares {DECLARED!r}")
    print(f"  new task declares      {DECLARED!r}              -> depends_on {got_same}")
    print(f"  new task declares      {REL_WRITE_FROM_SUBDIR!r} (same file, cwd units)")
    print(f"                                                   -> depends_on {got_mixed}")
    if not got_same:
        print("  DISCRIMINATOR BROKEN -- the control did not collide either, so this")
        print("  says nothing about mismatched units. Do not read a verdict off it.")
        return False
    return not got_mixed


def main() -> int:
    misplaced = report_placement()
    loud = report_loud()
    silent = report_silent()

    print("\n=== verdict ===")
    if not misplaced:
        print("  CANNOT FIRE -- a write lands in the declaration's units at every cwd tested.")
        print("  The invariant holds by construction and only a docstring is missing.")
        return 0

    print("  FIRES, under one condition: the session's cwd is not the repository root.")
    print(f"  LOUD  (compliant agent reported as diverging): {'YES' if loud else 'no'}")
    print(f"  SILENT (two agents handed one file, unreported): {'YES' if silent else 'no'}")
    print()
    print("  Severity follows the silent one. wrote_outside_declaration being wrong is")
    print("  an observation that has never yet been read -- Item 3 is unbuilt, so nothing")
    print("  surfaces it. _auto_depends is load-bearing TODAY, at every policy, and its")
    print("  own docstring calls it the entire mechanism keeping two agents out of one")
    print("  file. A units mismatch does not weaken it; it silently switches it off for")
    print("  the pair of tasks concerned.")
    print()
    print("  Not fixed here. Two candidate responses, both cheap, and the choice is the")
    print("  operator's: resolve the prefix once with `git rev-parse --show-prefix` at")
    print("  launch, or refuse to compute divergence when cwd is not a repository root.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
