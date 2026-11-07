"""
The board's words: how a text's standing, a task's stated property, what a
completion did not run and an incident mark read, on every surface that shows
them.

Each is a function of one value -- a ``Basis``, a string, a flag -- and of no
``Snapshot``, so this imports nothing from pptmstr when it runs. That is what lets
``approval``'s queue rows say what the board says without importing the board's
rows, which need ``model``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model import Basis


def basis_label(basis: Basis | None) -> str:
    """
    A text's standing as its author gave it, in the words every surface uses.

    **"says", because it is said.** The marker is the author's own account of how
    it knows -- nothing in the system checks whether anything was run -- so it is
    rendered as speech and never as a property of the text. "verified: run" would
    be the board vouching for a claim it only carried.

    **None is named rather than left blank.** An unmarked text beside marked ones
    reads as the settled kind, which is the misreading the marker exists to stop: a
    claim written into a specification looks established whether or not anybody
    established it. So the absence is shown, and it is shown as an absence -- not
    as the weakest member, which would be a standing nobody stated either.

    Here rather than in ``bus.py`` or the pane because both render it, and two
    spellings of one statement would be two boards.
    """
    return "basis unstated" if basis is None else f"says: {basis.value}"


def property_label(text: str) -> str:
    """
    A task's stated property as the line every surface puts under its title, or
    nothing when none was stated.

    Worded as the constraint it is, not as a field name: the reader is a builder
    deciding what it may trade away and a reviewer ranking what it found, and
    "must not break" is the question both are asking of the sentence.
    """
    return f"must not break: {text}" if text else ""


def not_run_label(not_run: str | None) -> str:
    """
    Where a completion stands on what it did not execute, in a few words that
    outlive a cut.

    **None is "unstated", never "nothing".** A result with no list of what was not
    run is the ordinary result, and it reads as verified exactly because nothing
    beside it says otherwise -- which is how artefacts that were written and never
    executed sat behind green test counts. So the absence is named on every
    completed row, and the one spelling that says nothing was left unrun is
    reserved for a claimer that stated it.

    **That statement is rendered as said**, for the reason ``basis_label`` gives:
    nothing here can check it.

    Text is not rendered here. It is prose with no natural bound, so each surface
    lays it out under its own budget and this says only that it is there.
    """
    if not_run is None:
        return "not run: unstated"
    if not not_run:
        return "says nothing was left unrun"
    return "says what was not run"


def incident_marker(incident: bool) -> str:
    """
    What goes ahead of a concern's subject when its sender marked it an incident,
    and nothing when it did not.

    Nothing rather than a "no incident" label, and the asymmetry is deliberate.
    The marker is something a sender sets; its absence is a sender that did not
    set it, which is most of them, including ones describing a slip in prose. A
    label on every unmarked concern would have the board saying of each that
    nothing went wrong, on no evidence.
    """
    return "INCIDENT " if incident else ""
