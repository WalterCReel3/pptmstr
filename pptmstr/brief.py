"""
A session's brief: premises on disk, appended to, derived on read.

**Append the amendments, derive the current brief.** That is ``STYLE.md`` §1's
*derive; do not store* applied across a restart boundary, and it is the same shape
the 08-14 record specifies for any store this project adds. Three arguments land on
it. Free mutation destroys the reason for putting premises on disk at all --
forensics asks *what did this worker act on*, and a file anyone can rewrite answers
*what did the file end up saying*, which differ in exactly the session that went
wrong. An append log also carries a clock the working tree does not: position in the
log is the version, monotonic and free, which is the staleness answer a sibling
record priced and rejected. And the codebase already runs this shape -- ``Transcript``
is append-only and ``STYLE.md`` lists it as a deliberate exception for the same
reason: a reader renders a consistent prefix while the writer keeps appending.

**One file per entry, not one growing file.** A write above ``PIPE_BUF`` is not
atomic, so appending to a single document lets a reader catch a partial record, and
rewriting a whole document per turn is what the 08-14 record calls *"not a slow path,
it is a corruption path"*. Each entry is written to a temp file in the same directory
and renamed over its target -- ``settings.save``'s primitive, named there as the right
one. No locking, no partial reads, no O(n^2). Directory listing gives the
consistent-prefix property ``Transcript`` gets from ``published_length``.

**There is no file called ``brief.md``, deliberately.** Append-only plus permitted
contradiction means a superseded premise is still on disk and still readable, so a
worker told to "read the brief" that opens one obvious filename acts on the version
that was overturned. The directory is the unit of reading, and the naming makes that
unavoidable rather than merely documented -- the footgun only surfaces in a session
that has already gone wrong, which is the worst place to discover it.

**Nothing here is stored in the snapshot.** Reading is IO and belongs in the shell;
the derived brief is a projection over the directory, rebuilt on demand. Persisting
it would violate *derive; do not store* on the way in -- which is the defect this
whole record is organised around, one layer up.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

# The first entry, and the only one that is not an amendment. Named rather than
# numbered at the call site so "is this the premises" is one comparison.
PREMISES = "premises"
AMENDMENT = "amendment"

# `000-premises.md`. Three digits because a brief with a thousand amendments has a
# problem no naming scheme fixes, and zero-padding is what makes lexical order and
# chronological order the same thing -- the derivation sorts by ordinal, but a
# worker reading the directory with `ls` gets the same sequence for free.
_ENTRY = re.compile(r"^(\d{3})-([a-z0-9-]+)\.md$")
_ORDINAL_WIDTH = 3

# `supersedes: 000, 001` on the first line, optional.
#
# Optional rather than required, which is the recorded lean and not yet a settled
# decision: requiring the link makes derivation exact and adds ceremony an operator
# writing at speed will skip, and a skipped link then reads as pure addition, which
# is silently wrong. Optional link plus strict chronological rendering is honest and
# cheap, and the first few real amendments answer it better than argument will.
_SUPERSEDES = re.compile(r"^\s*supersedes:\s*(.+?)\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class BriefEntry:
    """One file in the brief directory, as written."""

    ordinal: int
    kind: str
    path: Path
    body: str
    # Ordinals this entry declares it overturns. Empty is the common case and does
    # not mean "supersedes nothing was checked" -- it means the writer did not say.
    supersedes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DerivedEntry:
    """
    One entry as a reader should see it.

    ``superseded_by`` is the whole point of the derivation. Rendering only the
    current state would make an overturned premise silently *disappear* -- the log
    would exist and nobody reading could tell -- which is the ``Concern.edited``
    shape this project has already paid for once: a fact about the message that
    exists nowhere in the message. The premise stays visible and says who overturned
    it.
    """

    entry: BriefEntry
    superseded_by: tuple[int, ...]

    @property
    def is_superseded(self) -> bool:
        return bool(self.superseded_by)


def session_dir(root: Path, cwd: str, session_id: str) -> Path:
    """
    Where a session's brief lives: ``<root>/<cwd-slug>/briefs/<session-id>/``.

    Beside the transcripts that explain it rather than in the working tree, which
    the 08-14 record measured as where this project's durable state already is. In
    the tree it would dirty the working directory on every launch, straight into the
    untracked-file trap that cost a teammate's work once already.

    The slug is the CLI's own: absolute path with separators turned into dashes. It
    is reproduced rather than imported because nothing exposes it, and it is only a
    default -- ``LaunchSpec.brief`` carries whatever path the operator gave, which is
    what lets a fork inherit its parent's brief instead of deriving an empty one.
    """
    slug = str(Path(cwd).resolve()).replace(os.sep, "-")
    return root / slug / "briefs" / session_id


def write_entry(
    directory: Path, body: str, *, kind: str | None = None, supersedes: tuple[int, ...] = ()
) -> Path:
    """
    Append one entry. Returns the path written.

    The ordinal is taken from what is already on disk, so two writers racing produce
    two entries rather than one overwriting the other -- ``os.replace`` onto a name
    that already exists would silently destroy an entry, and a name derived from a
    count is exactly how that happens. The loop retries on collision rather than
    trusting the listing it just took.

    ``kind`` defaults to ``PREMISES`` for the first entry and ``AMENDMENT`` after,
    because that is what the ordinal already implies and a caller that had to state
    it is a caller that can state it wrongly.

    Raises ``OSError`` rather than swallowing it, unlike ``settings.save``. A theme
    that fails to persist costs a preference; premises that fail to persist are
    invisible until a worker acts on their absence, and the operator has to be told.
    """
    directory.mkdir(parents=True, exist_ok=True)
    existing = read_entries(directory)
    ordinal = (existing[-1].ordinal + 1) if existing else 0

    while True:
        name = kind or (PREMISES if ordinal == 0 else AMENDMENT)
        target = directory / f"{ordinal:0{_ORDINAL_WIDTH}d}-{name}.md"
        if not target.exists():
            break
        ordinal += 1

    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=".entry-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if supersedes:
                handle.write(
                    f"supersedes: {', '.join(f'{o:0{_ORDINAL_WIDTH}d}' for o in supersedes)}\n\n"
                )
            handle.write(body.rstrip("\n") + "\n")
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return target


def read_entries(directory: Path) -> tuple[BriefEntry, ...]:
    """
    Every entry in the directory, oldest first.

    A missing directory is an empty brief, not an error: a session launched without
    one is the common case, and a caller that had to distinguish "no brief" from "no
    directory yet" would be distinguishing two spellings of the same fact.

    Files that do not match the naming are skipped rather than reported. The
    directory holds temp files mid-write (``.entry-*``), and a reader that tripped
    over one would fail exactly while a write was in flight -- which is the moment
    the consistent-prefix property exists to protect.
    """
    try:
        names = sorted(p.name for p in directory.iterdir())
    except OSError:
        return ()

    out = []
    for name in names:
        match = _ENTRY.match(name)
        if match is None:
            continue
        path = directory / name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        supersedes, body = _split_header(text)
        out.append(
            BriefEntry(
                ordinal=int(match.group(1)),
                kind=match.group(2),
                path=path,
                body=body,
                supersedes=supersedes,
            )
        )
    return tuple(sorted(out, key=lambda e: e.ordinal))


def derive(entries: tuple[BriefEntry, ...]) -> tuple[DerivedEntry, ...]:
    """
    The brief a reader should see: every entry, in order, marked where overturned.

    Pure, and separate from the reading above so the rule can be exercised without a
    filesystem. Nothing is dropped and nothing is merged -- an entry that was
    overturned is rendered *as overturned* rather than removed, which is obligation 1
    and the reason the log is worth keeping at all.

    Only a *later* entry can supersede an earlier one. An entry naming a higher
    ordinal is ignored rather than honoured: the log's order is its version, so
    letting an earlier entry overturn a later one would make the sequence stop
    meaning anything. Naming an ordinal that does not exist is likewise ignored --
    it is a typo, and the alternative is a brief that refuses to render.
    """
    by_ordinal = {e.ordinal: e for e in entries}
    return tuple(
        DerivedEntry(
            entry=entry,
            superseded_by=tuple(
                later.ordinal
                for later in entries
                if later.ordinal > entry.ordinal and entry.ordinal in later.supersedes
            ),
        )
        for entry in entries
        if entry.ordinal in by_ordinal
    )


def _split_header(text: str) -> tuple[tuple[int, ...], str]:
    """
    The declared supersessions and the body they preceded.

    Only the first line is examined. A ``supersedes:`` further down is prose -- an
    operator writing *"this supersedes the earlier assumption"* mid-paragraph means
    it as English, and reading it as a directive would let the body rewrite the
    log's structure from inside.
    """
    first, _, rest = text.partition("\n")
    match = _SUPERSEDES.match(first)
    if match is None:
        return (), text.strip("\n")

    ordinals = []
    for token in match.group(1).replace(",", " ").split():
        with contextlib.suppress(ValueError):
            ordinals.append(int(token))
    return tuple(ordinals), rest.strip("\n")
