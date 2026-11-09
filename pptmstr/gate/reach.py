"""
Where the paths a call names lead, measured against pptmstr's records.

``records_read`` says why a call would read another session's records and
``records_written`` why it would write under any session's, each by resolving
every path the call names -- ``~``, ``..`` and symlinks followed -- against the
records roots under ``settings.config_dir()``. That is the disk this module
touches, and all of it: where a path leads is a fact about the disk and not about
its spelling. Nothing here reads a file's contents or writes.

No SDK and no UI. ``gate.classify`` asks both of a call before it admits one,
and ``gate.park`` leads a queue row with what they say.
"""

from __future__ import annotations

import os
import shlex
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import settings
from ..model import written_path
from ..shellscan import _split_segments, is_read_only
from .tools import FILE_READS, TREE_READS

# The two directories pptmstr keeps sessions' records under, as names beneath
# ``settings.config_dir()``. Spelled here rather than imported from ``sessionfile``
# and ``boardsync``, though neither import would close a cycle and the store does
# not import this module: one table of the records roots, which
# ``gate.sandbox.denied_record_writes`` spells a third time, is a follow-up of
# planning/2026-11-07-board-and-gate-packages.md §2. tests/test_approval.py holds
# both to ``sessionfile.root()`` and ``boardsync.legacy_root()``.
_RECORD_ROOTS = ("sessions", "boards")

# Where briefs are written (``brief.default_root``), spelled here as the two above
# are. A root for ``records_written`` and not for ``records_read``: a
# worker reads its premises and a fork reads its parent's, and only pptmstr
# appends to them, so a tool call that writes one has rewritten what a session
# acted on. tests/test_approval.py holds it to ``brief.default_root()``.
_WRITE_ONLY_ROOTS = ("briefs",)

# What makes a word a pattern over file names rather than one name: to bash, and
# to the CLI's ``Glob``, which reads braces as well.
_PATTERN_CHARS = frozenset("*?[{")

# The admitted commands that read the files they are handed and nothing else.
# Handed a directory each fails or reports on the directory itself, and with no
# operand each reads standard input or nothing, so none of them reads a tree and
# none reads the working directory. Every other command ``shellscan`` admits --
# ``ls``, ``du``, ``grep``, ``git``, and any row added after this was written --
# is taken to read the whole of every directory it names and of the one it runs
# in, because which flags make one descend is the binary's knowledge.
_READS_ONLY_ITS_OPERANDS = frozenset(
    {"cat", "head", "tail", "wc", "nl", "sed", "stat", "echo", "pwd", "df", "which", "cd"}
)

# The most directories a command may be running in before ``records_read`` stops
# following it. Every ``cd`` can double the count, since a ``cd`` in a pipeline or
# behind ``||`` may not have happened, and ``shellscan`` admits eight segments.
_MAX_DIRECTORIES = 64


@dataclass(frozen=True, slots=True)
class _Records:
    """
    Where sessions' records are, and which of those directories is the caller's.

    Every path is one ``_place`` produced, so a comparison here is between two
    locations and never between two spellings.
    """

    roots: tuple[Path, ...]
    own: tuple[Path, ...]

    def holding(self, place: Path) -> Path | None:
        """
        The records root ``place`` is in, the root itself included, or None when it
        is in none or is in the caller's own directory.

        The root counts because listing it names every other session, and a session
        id is the directory another session's command output is under.
        """
        if any(place == mine or mine in place.parents for mine in self.own):
            return None
        return next((r for r in self.roots if place == r or r in place.parents), None)

    def beneath(self, place: Path) -> Path | None:
        """
        The records root somewhere under ``place``, or None.
        """
        return next((r for r in self.roots if place in r.parents), None)


def _place(raw: str, base: Path) -> Path | None:
    """
    ``raw`` as one absolute path with its symlinks followed, measured from ``base``
    when it is relative, or None when the filesystem cannot say where it is.

    ``driver._located`` is the same function for the write bound and gives the same
    reasons: None for a symlink loop, a ``~user`` with no home or an embedded NUL,
    and never a raise, because every string here came from a model and an exception
    would run up through ``PreToolUse`` into the CLI.
    """
    try:
        return (base / Path(raw).expanduser()).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _configuration() -> Path:
    """
    The directory the records roots are named under.

    Through the module and at call time, as ``sessionfile._under`` does and for its
    reason: a test redirects ``settings.config_dir``. A function of its own, taking
    nothing, so that tests/test_settings.py finds it and holds what it answers
    inside the suite's temporary tree.
    """
    return settings.config_dir()


def _records(session_id: str | None, names: tuple[str, ...] = _RECORD_ROOTS) -> _Records | None:
    """
    The records roots and the caller's own directories under them, or None when
    the configuration directory cannot be located.

    Each root is given twice when it is reached through a symlink, as it is spelled
    and as it resolves: a search rooted above the spelled one is as much a read of
    the records as one rooted above where they are.

    A session's own directory is ``<root>/<session id>`` only when that is a real
    entry of the root under that exact name. An id that is not one directory name
    -- empty, ``..``, one with a separator -- has no directory, which is
    ``journal.files.session_dir``'s rule reached by resolving rather than by listing
    the spellings. A symlink at that name is not the caller's either: what it
    leads to is judged as wherever it leads, which is what makes the link
    ``boardsync.migrate`` leaves at ``boards/<id>`` readable for the session it
    belongs to and for no other.

    With no ``session_id`` nothing is the caller's, so everything under a root is
    somebody else's.

    ``names`` are the roots, beneath the configuration directory.
    """
    try:
        spelled = [_configuration() / name for name in names]
    except (OSError, RuntimeError):
        return None
    roots: list[Path] = []
    for directory in spelled:
        resolved = _place(str(directory), Path("/"))
        if resolved is None:
            return None
        roots.extend(r for r in (resolved, Path(os.path.abspath(directory))) if r not in roots)
    own: list[Path] = []
    for root in roots:
        mine = _place(session_id, root) if session_id else None
        if mine is not None and mine.parent == root and mine.name == session_id:
            own.append(mine)
    return _Records(tuple(roots), tuple(own))


def _spellings(raw: str) -> tuple[str, ...]:
    """
    ``raw`` and, when it differs, ``raw`` without the whitespace around it.

    ``Path(" /x")`` is relative, so a padded absolute path is one name to this
    module and may be another to the tool that opens it. A write is refused over
    that disagreement (``driver._escapes_write_region``); a read is judged under
    both readings, and either one landing in the records is enough.
    """
    return (raw,) if raw == raw.strip() else (raw, raw.strip())


def _fixed_part(pattern: str) -> str:
    """
    The directory a pattern cannot match outside of: its leading components up to
    the first that carries a pattern character, or all of it when none does.
    """
    cut = next((i for i, char in enumerate(pattern) if char in _PATTERN_CHARS), None)
    if cut is None:
        return pattern
    return pattern[: pattern.rfind("/", 0, cut) + 1] or "."


def _names_in(word: str) -> Iterator[str]:
    """
    Every string in one shell word that the command might read as a path: the word,
    what follows an ``=`` in it, and what follows the letter of a short option.

    Which options take a file is the binary's knowledge, so all three are offered
    for every word. A word that was never a path resolves to a name under the
    working directory that does not exist, which is in nobody's records.
    """
    yield word
    if "=" in word:
        yield word.split("=", 1)[1]
    if word.startswith("-") and not word.startswith("--"):
        yield word[2:]


def _shell_places(command: str, start: Path) -> list[tuple[Path, bool]] | None:
    """
    Every place a ``shellscan``-admitted command reads, each with whether it is
    read as a tree, or None when one of them cannot be placed.

    Only for a command ``shellscan.is_read_only`` passed. That is what makes the
    words the arguments: such a command carries no ``$``, no substitution, no
    brace and no unquoted leading ``~``, so the only thing bash does to a word
    after ``shlex`` has read it is match a glob against file names, and
    ``_fixed_part`` is the directory that matching cannot leave. A word with a
    glob in it is therefore a tree, whatever command it is handed to.

    **A command reads its working directory, as a tree, unless it is one of
    ``_READS_ONLY_ITS_OPERANDS``.** ``grep -r token`` and ``ls -R`` name no path.

    **A command is followed through its ``cd``s, into every directory it may be
    in.** A ``cd`` in a pipeline runs in a subshell and one behind ``||`` may not
    run, so the directories before it stay candidates beside the ones after, and
    every later word is placed from each. Bash's ``cd`` drops a ``..`` from the
    spelling before it follows a symlink and the kernel does the opposite for
    every other command, so a ``cd`` adds both readings. A ``cd`` reads nothing
    itself. ``cd -`` goes to a directory only the shell knows: after one, nothing
    that depends on the working directory can be placed.

    Not followed: ``CDPATH``, which sends a relative ``cd`` somewhere this cannot
    see, and a symlink a recursive command walks through under a directory that
    is itself outside the records.
    """
    spelled = [start]
    lost = False
    places: list[tuple[Path, bool]] = []
    for segment in _split_segments(command):
        try:
            argv = shlex.split(segment)
        except ValueError:
            return None
        if not argv:
            continue
        moving = argv[0] == "cd"
        descends = argv[0] not in _READS_ONLY_ITS_OPERANDS
        if descends:
            if lost:
                return None
            for directory in spelled:
                here = _place(".", directory)
                if here is None:
                    return None
                places.append((here, True))
        arrived: list[Path] = []
        for word in argv[1:]:
            if moving and word == "-":
                lost = True
                continue
            for name in _names_in(word):
                fixed = _fixed_part(name)
                try:
                    relative = not Path(fixed).expanduser().is_absolute()
                except (OSError, RuntimeError, ValueError):
                    return None
                if lost and relative:
                    return None
                for directory in spelled:
                    placed = _place(fixed, directory)
                    if placed is None:
                        return None
                    if moving:
                        dotted = os.path.normpath(directory / Path(fixed).expanduser())
                        arrived.extend((placed, Path(dotted)))
                    else:
                        places.append((placed, descends or fixed != name))
        spelled.extend(p for p in dict.fromkeys(arrived) if p not in spelled)
        if len(spelled) > _MAX_DIRECTORIES:
            return None
    return places


def _named_places(
    tool_name: str, tool_input: Mapping[str, Any], base: Path
) -> list[tuple[Path, bool]] | None:
    """
    The places a reading call names, each with whether it is read as a tree, or
    None when one of them cannot be placed. An empty list is a call that names
    nothing this function reads.
    """

    def placed(raw: object, origins: list[Path]) -> list[Path] | None:
        if not isinstance(raw, str):
            return None
        found: list[Path] = []
        for spelling in _spellings(raw):
            for origin in origins:
                place = _place(spelling, origin)
                if place is None:
                    return None
                found.append(place)
        return found

    if tool_name in FILE_READS:
        raw = tool_input.get(FILE_READS[tool_name])
        # No path is a call the tool refuses for want of one, and reads nothing.
        if raw is None:
            return []
        files = placed(raw, [base])
        return None if files is None else [(place, False) for place in files]

    if tool_name in TREE_READS:
        # An absent ``path`` is the working directory, to both tools.
        trees = placed(tool_input.get("path") or ".", [base])
        pattern = tool_input.get("pattern")
        if trees is not None and tool_name == "Glob" and pattern is not None:
            # A pattern may climb out of ``path`` or be absolute, so where it can
            # match is measured from each place ``path`` may be. ``Grep``'s
            # pattern is a regular expression over contents and names no file;
            # its ``glob`` filters names under ``path`` and does not widen it.
            trees = placed(_fixed_part(pattern) if isinstance(pattern, str) else pattern, trees)
        return None if trees is None else [(place, True) for place in trees]

    if tool_name == "Bash":
        command = tool_input.get("command")
        # A command ``shellscan`` does not vouch for is one whose words are not
        # known to be its arguments, and no rung admits it on its words.
        if not isinstance(command, str) or not is_read_only(command):
            return []
        return _shell_places(command, base)

    return []


def records_read(
    tool_name: str,
    tool_input: Mapping[str, Any],
    *,
    session_id: str | None = None,
    cwd: str | None = None,
) -> str | None:
    """
    Why this call would read another session's records, or None when it would not.

    pptmstr keeps what it knows of every session under one directory: the
    commands a session asked the operator to run and what they printed, the
    operator's reasons for refusing them, and its board. ``sandbox`` denies that
    directory to a contained ``Bash`` command and to nothing else -- ``Read``,
    ``Glob``, ``Grep`` and ``NotebookRead`` run in the CLI's own process, and an
    uncontained session's ``Bash`` has no sandbox -- so this is what stands
    between those and the records. ``classify`` asks it of every call it would
    otherwise admit by one of those names.

    **The rule is about where a path leads.** Each named path is resolved from
    ``cwd`` with ``~``, ``..`` and symlinks followed, and the call reads the
    records when one of them is under a records root and not under
    ``<root>/<session_id>``. The root itself is not the caller's. The legacy
    ``boards`` directory is a root on the same terms, and the link left there for
    a moved log is followed to the session it belongs to.

    **A search is a read of everything under where it starts.** ``Glob``,
    ``Grep``, a shell word with a glob in it, and every admitted shell command
    outside ``_READS_ONLY_ITS_OPERANDS`` are treated as reading the whole of each
    directory they name, so one that names a directory *containing* a records
    root -- ``~``, ``~/.config``, ``/`` -- reads the records. A pattern is
    narrowed to its leading components without a pattern character first, so
    ``Source/**/*.py`` from the home directory is a search of ``Source``. The
    price is every search rooted above the configuration directory, and for a
    shell command it is wider than it sounds: ``ls /`` lists one level and is
    treated as ``ls -R /`` would be. A session whose own ``cwd`` contains the
    records has no admitted ``ls``, ``du``, ``grep`` or ``git`` and no admitted
    glob that starts in it, and no ``Glob`` or ``Grep`` without a ``path``.

    **What cannot be placed is treated as a read.** A path with an embedded NUL,
    a symlink loop, an argument that is not text, a relative path behind a
    ``cd -``, or a configuration directory that cannot be located: each is a
    case where staying out of the records cannot be shown.

    **``session_id`` and ``cwd`` are the driver's, never the call's.** With no
    ``session_id`` no directory is the caller's and everything under a root is
    another session's. With no ``cwd`` a relative path is measured from this
    process's directory, which is where a session that named none runs
    (``driver.AgentSession._write_region``) and is the wrong base for any other.

    **Decided before the call runs**, as the write bound is: a component that
    becomes a symlink between this answer and the read is not seen. Not covered at
    all: a hard link, an MCP tool that reads files, ``WebFetch`` of a ``file:``
    URL, and ``Bash`` under ``AUTONOMOUS``, where the command is not one
    ``shellscan`` read and the sandbox is the bound.
    """
    if tool_name not in FILE_READS and tool_name not in TREE_READS and tool_name != "Bash":
        return None
    whose = "another session's" if session_id else "a session's"
    unplaced = (
        f"{tool_name} names a path this build cannot place, so it cannot be shown to "
        f"stay out of {whose} records, which pptmstr keeps apart from every other session"
    )
    try:
        base = Path(os.path.abspath(Path(cwd or ".").expanduser()))
    except (OSError, RuntimeError, ValueError):
        return unplaced
    places = _named_places(tool_name, tool_input, base)
    if places is None:
        return unplaced
    if not places:
        return None
    records = _records(session_id)
    if records is None:
        return unplaced
    mine = (
        f"this session's own are under {records.own[0]}"
        if records.own
        else "which directory under it is this session's is not known to the gate"
    )
    for place, tree in places:
        root = records.holding(place)
        if root is not None:
            return (
                f"{tool_name} reads {place}, which is in {whose} records: pptmstr "
                f"keeps every session's command output, board and refusals under "
                f"{root}, and {mine}"
            )
        root = records.beneath(place) if tree else None
        if root is not None:
            return (
                f"{tool_name} reads everything under {place}, and {whose} records are "
                f"under it: pptmstr keeps every session's command output, board and "
                f"refusals in {root}, and {mine}. Name a directory that does not "
                f"contain it"
            )
    return None


def records_written(
    tool_name: str, tool_input: Mapping[str, Any], *, cwd: str | None = None
) -> str | None:
    """
    Why this call would write under a records root, or None when it would not.

    pptmstr reads what is under the roots back as what happened: a session's
    ``session.json``, its board log, and a command's ``started.json``, ``end.json``
    and ``gone.json`` decide what the operator is shown and what a restart
    restores. Only pptmstr writes them, from its own process and never through a
    tool call, so **every** session's directory counts here, the caller's own
    included, and ``classify`` admits no write that names one on any rung.

    **Briefs are a root here and not in ``records_read``.** They are premises a
    session acts on and are read by it, and pptmstr is their only writer
    (``_WRITE_ONLY_ROOTS``).

    The four write tools only, and their target is the one ``model.written_path``
    reads, which is the argument the write bound and the ledger read. It is placed
    as ``records_read`` places a path -- from ``cwd``, with ``~``, ``..`` and
    symlinks followed, both spellings of a padded one -- against the same roots,
    the legacy ``boards`` directory among them, and the briefs root. A target that
    cannot be placed and a configuration directory that cannot be located are each
    treated as a write into the records. A write tool that names no target is not: it opens nothing,
    and where it would be released the driver's write bound refuses it for that
    (``driver._escapes_write_region``).

    ``Bash`` is not asked. ``PERMISSIVE`` admits only a command ``shellscan`` says
    writes nothing, and ``AUTONOMOUS`` hands the command to the sandbox, whose
    write region is the session's directory: what keeps a contained command out
    of the records when that directory contains them is the sandbox's own
    configuration (``gate.sandbox.containment_settings``), not this gate.

    Decided before the call runs, with ``records_read``'s limits: a component that
    becomes a symlink afterwards, and a hard link, are not seen.
    """
    raw = written_path(tool_name, tool_input)
    if raw is None:
        return None
    unplaced = (
        f"{tool_name} names a target this build cannot place, so it cannot be shown "
        f"to stay out of pptmstr's own records"
    )
    try:
        base = Path(os.path.abspath(Path(cwd or ".").expanduser()))
    except (OSError, RuntimeError, ValueError):
        return unplaced
    records = _records(None, _RECORD_ROOTS + _WRITE_ONLY_ROOTS)
    if records is None:
        return unplaced
    for spelling in _spellings(raw):
        place = _place(spelling, base)
        if place is None:
            return unplaced
        root = records.holding(place)
        if root is not None:
            return (
                f"{tool_name} writes {place}, which is in pptmstr's own records under "
                f"{root}: pptmstr reads them back as what happened in a session, so "
                f"no agent writes there unasked, this session's own directory included"
            )
    return None
