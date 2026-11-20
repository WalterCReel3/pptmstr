"""
The queue row a parked call is shown as, and what DETAIL says of its provenance.

Pure: no SDK, no UI, no clock, no IO. Every function here reads a tool's name and
its arguments and nothing else, which is what lets the store compose a row
(``compose``) when a link changes. The one fact about a call that needs the disk
-- whose records it reads or writes -- is measured in ``gate.park`` and handed in
as ``compose``'s ``mark``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, assert_never

from ..board.words import basis_label, incident_marker, property_label
from ..channels.records import Charter
from ..commandtext import visible
from ..model import (
    Basis,
    Provenance,
    kept_provenance,
    normalised_property,
    stated_flag,
    stated_incident,
    stated_provenance,
)
from .tools import (
    _BUS_AMEND,
    _BUS_DECLARE,
    _BUS_PEER,
    _BUS_POST,
    _BUS_WITHDRAW,
    _BUS_WITHDRAW_PEER,
    _reviewed_by_arguments,
    states_provenance,
)


def command_shown(tool_name: str, key: str, value: object) -> str | None:
    """
    A Bash call's command as every approval surface draws it, or None when
    ``key`` of ``tool_name`` is not one.

    ``commandtext.visible`` and nothing else: a newline is the two characters
    backslash-n followed by the break itself, so a line break in the command
    is never drawn as a space, and never as a break the pane could have made
    by wrapping. `ls # '` + newline + `touch pwned #'` is two commands to bash
    (``shellscan._scan``), and flattened to `ls # ' touch pwned #'` the second
    is comment text to the eye. Since a command carrying a comment parks, that
    row is the one the operator approves from.

    One function so that the queue row, the expanded row and the detail pane
    cannot each have a rule: ``summarize`` flattens this to one line, where
    the marker survives and the break does not, and the two panes draw it
    line for line.

    What it costs an ordinary command is ``visible``'s whole scheme rather
    than its newline rule alone. A backslash is drawn doubled, so
    `grep 'a\\.b' f` reads `grep 'a\\\\.b' f`; a tab is `\\x09`; a character
    outside ASCII is `\\u{...}`. A command with none of those is drawn as
    typed. Taking only the newline rule would leave a carriage return and a
    bidirectional override drawn as nothing, and a literal backslash-n
    indistinguishable from a marked break.
    """
    if tool_name != "Bash" or key != "command" or not isinstance(value, str):
        return None
    return visible(value)


# How much of the subject a row that reaches another team's lead keeps when
# even the shortest description of that lead leaves it less (``summarize``).
_SUBJECT_KEPT = 40


@dataclass(frozen=True, slots=True)
class Summary:
    """
    A queue row (``summary_of``), and where in it the description of the lead
    a call reaches stands: ``peer`` is that span of ``text``, or None on a row
    that names no lead -- every row but a call across a link, and one that
    says why it reaches nobody.
    """

    text: str
    peer: tuple[int, int] | None = None


def compose(
    tool_name: str,
    tool_input: Mapping[str, Any],
    width: int = 90,
    *,
    mark: str = "",
    reaches: str | Sequence[str] | None = None,
    across: str | None = None,
) -> Summary:
    """
    One line naming what the call would do. This is the queue row.

    Reads as an action rather than as a serialised argument dict, because the queue
    is scanned rather than read -- the operator is deciding which item to look at,
    not deciding the item.

    **The path comes from the key the tool declares**, which is the same rule
    ``model.written_path`` applies and has to be the same rule. This is the row a
    human approves from and that one is what the ledger measures and what the gate
    bounds, so a call carrying both keys must not be able to show one path here and
    act on the other -- a decoy that fools the reviewer is worse than one that only
    fools the measurement, because the reviewer is what the measurement is for.

    ``"?"`` when the declared key is absent, and that is the honest row rather than
    a gap: the other key being filled in does not make it the target, and a call
    whose destination cannot be read is one the operator should open.

    **A message, a declaration and an amendment say what their author said about
    them**, in the words the board will use once they land (``board.words.basis_label``
    and its neighbours, and ``model``'s reading of the raw arguments). This is the
    row the operator signs the text off from, so a standing the board shows
    afterwards and this row did not would be a claim approved unseen. The standing and the
    incident marker ride ahead of the subject or title because the row is clipped
    from the right and they are the short part. A declaration's property goes
    last for the opposite reason: it is a whole sentence that will rarely fit, and
    at the end a cut shows as the ellipsis it is, where in the middle it would
    push the size off the row and still not be whole. A declaration another lead
    asked for carries its charter mark and channel beside its standing, for the
    same reason and because they are what the operator judges it on. The detail
    pane lists every argument uncut.

    **``mark`` leads the row, ahead of everything else.** It is the records
    mark ``gate.park.summary_of`` measures on the disk -- whose records a call
    reads or writes -- and is given here so that composing a row touches no
    disk. The store gives none: every call it composes a row for is one the
    records checks return None for by its name alone.

    **A call that reaches another team's lead names whom** (``peer_channel``):
    ``reaches`` is the session at the other end of the link as the store sees
    it now, which only the store can say. The store writes it onto the row when
    the call parks and again whenever a link changes, so a name closed and
    linked again under the operator's eye shows the session it would go to.
    A ``withdraw_peer_message`` names no channel, so ``across`` is the name of
    the link the store found the withdrawn message on, or None where it found
    none.

    **On such a row the subject is what is being sent, so it outlasts the
    description of whom.** ``reaches`` may be several descriptions of one
    session, longest first (``effects.peer_text``'s shorter forms): the row
    takes the longest that leaves the subject whole within ``width``, else the
    shortest, and the shortest still leaves ``_SUBJECT_KEPT`` characters of the
    subject on the row, past ``width`` if it must. ``Summary.peer`` is where the
    description taken stands on the row, with its spaces run together as the
    row runs them, whole, since the row is cut only past the head; None when
    ``reaches`` is a sentence saying why nobody is reached.
    """

    def standing(raw: object) -> str:
        try:
            return basis_label(Basis.stated(raw))
        except ValueError:
            # The handler refuses this call. Shown as written, so the operator
            # approving it knows the approval will not put anything on the board.
            return f"basis {str(raw)!r} is not one the bus accepts"

    def flat(text: str) -> str:
        return mark + " ".join(text.split())

    def clip(text: str, limit: int = width) -> Summary:
        text = flat(text)
        return Summary(text if len(text) <= limit else text[: limit - 3] + "...")

    def reaching(before: str, after: str, subject: str) -> Summary:
        # The row is ``before``, whom the call reaches in parentheses, ``after``
        # and the subject. A description is said flattened, so its span on the
        # flattened row starts right after the flattened "(" ahead of it.
        forms: list[str | None]
        if reaches is None or isinstance(reaches, str):
            named, forms = False, [reaches]
        else:
            named, forms = True, [" ".join(f.split()) for f in reaches]
        heads = [(f, before + ("" if f is None else f" ({f})") + after) for f in forms or [None]]
        form, head = heads[-1]
        limit = max(width, len(flat(head)) + 1 + _SUBJECT_KEPT)
        for candidate in heads:
            if len(flat(candidate[1] + subject)) <= width:
                (form, head), limit = candidate, width
                break
        row = clip(head + subject, limit)
        if not named or not form:
            return row
        # ``limit`` leaves the whole head on the row, so the clip never cuts
        # the description.
        start = len(flat(before + " ("))
        return Summary(row.text, (start, start + len(form)))

    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        declared = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
        path = str(tool_input.get(declared) or "?")
        return clip(f"{tool_name} {path}")
    if tool_name == "Bash":
        # The one row that is not a description of the call but the call, so it
        # is the one row where the flattening below could hide something: see
        # `command_shown`. The count leads because the row is clipped from the
        # right, and a second line past the cut must not read as a long first.
        shown = command_shown(tool_name, "command", str(tool_input.get("command") or "")) or ""
        lines = shown.count("\n") + 1
        return clip(shown if lines == 1 else f"{lines} lines · {shown}")
    if tool_name in ("WebFetch", "WebSearch"):
        return clip(f"{tool_name} {tool_input.get('url') or tool_input.get('query') or ''}")
    if tool_name in ("Task", "Agent"):
        subtype = tool_input.get("subagent_type") or "agent"
        return clip(f"spawn {subtype}: {tool_input.get('description') or ''}")
    if tool_name == _BUS_POST:
        # The row an operator scans to decide whether to open it, so it leads with
        # who is being told what. The body is the diff-equivalent and belongs in the
        # detail pane, not here.
        to = tool_input.get("to") or "?"
        subject = str(tool_input.get("subject") or "").strip()
        marker = incident_marker(stated_incident(tool_input.get("incident")))
        return clip(
            f"message {to} ({standing(tool_input.get('basis'))}): "
            f"{marker}{subject or tool_input.get('body') or ''}"
        )
    if tool_name == _BUS_PEER:
        # A message to another team's lead, led by the channel it travels on:
        # the operator made the link, and which one this is is the first thing
        # to judge it by. An argument the handler refuses is said, so approving
        # it is not mistaken for sending it.
        channel = str(tool_input.get("channel") or "?").strip()
        subject = str(tool_input.get("subject") or "").strip()
        asks = ""
        if stated_flag(tool_input.get("needs_answer")):
            asks += ", needs an answer"
        if "incident" in tool_input:
            asks += ", carries an incident, which is refused"
        return reaching(
            f"message the lead on #{channel}",
            f"{asks}: ",
            str(subject or tool_input.get("body") or ""),
        )
    if tool_name == _BUS_WITHDRAW_PEER:
        # A withdrawal parks only for its reason, which the other lead reads,
        # so the reason is the subject. The channel is the store's (``across``):
        # the call names a message, not a link.
        message = " ".join(str(tool_input.get("message_id") or "").split())
        what = f"withdraw {message}" if message else "withdraw a message it does not name"
        on = "" if across is None else f" on #{across}"
        reason = str(tool_input.get("reason") or "").strip()
        return reaching(
            f"{what}{on}", ", " + ("saying: " if reason else ""), reason or "giving no reason"
        )
    if _reviewed_by_arguments(tool_name, tool_input):
        # A decision the other link's lead reads. It parks only for that
        # reader, so the row leads with the channel and whom it reaches, then
        # the question.
        channel = str(tool_input.get("channel") or "").strip()
        return reaching(
            f"decision read by the lead on #{channel}", ": ", str(tool_input.get("question") or "")
        )
    if tool_name == _BUS_DECLARE:
        # The question at this row is "should this work exist", so it leads with the
        # claim. The specification is the diff-equivalent and belongs in the detail
        # pane; what rides along here is the *size* -- how many files it will write
        # and whether it is sequenced behind anything -- because sign-off is a
        # scoping moment and those two are what the operator would otherwise open
        # the row to find.
        #
        # Where a peer's request came from rides inside the parentheses, beside
        # the standing and ahead of the title: on a reviewed rung this row is
        # where the operator judges the request against the charter, and a title
        # of ordinary length already fills the row the driver builds. The mark
        # leads the channel because a channel name may be as long as an id. The
        # message id is left to DETAIL, which says the whole sentence. A
        # declaration that gives none adds nothing here.
        #
        # A dry run that parked -- any spelling ``_dry_run`` does not admit -- leads
        # with that, read by the handler's own rule (``stated_flag``), so a row
        # approved as a declaration is one the handler declares. A value the
        # handler refuses says so in the same place, as ``standing`` does.
        title = str(tool_input.get("title") or "").strip()
        said = standing(tool_input.get("basis")) + _row_provenance(tool_input)
        rehearsed = stated_flag(tool_input.get("dry_run"))
        lead = (
            "DRY RUN, declares nothing: "
            if rehearsed
            else (
                f"dry_run {str(tool_input.get('dry_run'))!r} is not one the bus accepts: "
                if rehearsed is None
                else ""
            )
        )
        parts = [f"{lead}declare ({said}) {title or '(untitled)'}"]
        touches = tool_input.get("touches") or ()
        if touches:
            parts.append(f"writes {', '.join(str(p) for p in touches)}")
        depends = tool_input.get("depends_on") or ()
        if depends:
            parts.append(f"after {', '.join(str(d) for d in depends)}")
        held_to = property_label(normalised_property(tool_input.get("property")))
        if held_to:
            parts.append(held_to)
        return clip(" · ".join(parts))
    if tool_name == _BUS_AMEND:
        # The question at this row is "may this replace what the task is held to".
        # Which task and how the amender stands behind the new text lead; what it
        # does to the property follows, because that sentence is the bar and an
        # amendment that moves or removes it is the one an operator most needs to
        # open. The text itself is the diff-equivalent, as a declaration's is.
        #
        # A basis is shown only beside a text it can end up beside. With no
        # ``detail``, or a blank one, the handler leaves the text standing and
        # the reducer keeps the basis its author gave it, so the amender's is
        # discarded: printed here, it would be a standing the operator signed
        # off and the board never shows. One the bus will not accept is still
        # shown, since that call is refused whatever else it carries.
        task = str(tool_input.get("task_id") or "?").strip()
        sent, raw = tool_input.get("detail"), tool_input.get("basis")
        said = standing(raw)
        if sent is None or not str(sent).strip():
            try:
                given = Basis.stated(raw)
            except ValueError:
                pass
            else:
                said = "its text stands" + (
                    "" if given is None else "; the basis given is not kept"
                )
        parts = [f"amend {task} ({said})"]
        held_to = property_label(normalised_property(tool_input.get("property")))
        if stated_flag(tool_input.get("withdraw_property")):
            parts.append("WITHDRAWS its property")
        elif held_to:
            parts.append(f"replaces its property -- {held_to}")
        return clip(" · ".join(parts))
    if tool_name == _BUS_WITHDRAW:
        # The question at this row is "may this work stop being wanted", so it
        # leads with which task. The reason follows, since it is what the holder
        # and every reader of the row will be given in place of a result.
        task = str(tool_input.get("task_id") or "?").strip()
        reason = " ".join(str(tool_input.get("reason") or "").split())
        return clip(f"withdraw {task}: {reason or '(no reason given)'}")
    if not tool_input:
        return clip(tool_name)
    key = next(iter(tool_input))
    return clip(f"{tool_name} {key}={tool_input[key]!r}")


def _row_provenance(tool_input: Mapping[str, Any]) -> str:
    """
    A declaration's provenance as the queue row carries it, after its standing:
    "; charter: yes on #brand", nothing when it gives none, and that the bus
    refuses it when it does -- the reason is DETAIL's (``declared_provenance``).
    """
    try:
        asked = stated_provenance(
            tool_input.get("channel"), tool_input.get("re"), tool_input.get("inside_charter")
        )
    except ValueError:
        return "; provenance the bus refuses"
    if asked is None:
        return ""
    return f"; charter: {asked.inside_charter.value} on #{asked.channel}"


@dataclass(frozen=True, slots=True)
class _Refused:
    """
    Arguments the bus would refuse for their provenance, and the sentence it
    would refuse them with. Apart from None, which is arguments that state no
    provenance and are placed without one.
    """

    reason: str


def _provenance_read(tool_input: Mapping[str, Any]) -> Provenance | None | _Refused:
    """What a declaration's arguments state as a provenance, by the bus's rule."""
    try:
        return stated_provenance(
            tool_input.get("channel"), tool_input.get("re"), tool_input.get("inside_charter")
        )
    except ValueError as bad:
        return _Refused(str(bad))


def _canonical(args: Mapping[str, Any]) -> str:
    """``args`` as JSON with its keys sorted and no whitespace (``is_edit``)."""
    return json.dumps(dict(args), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def is_edit(sent: Mapping[str, Any], parked: Mapping[str, Any]) -> bool:
    """
    Whether arguments ``sent`` for a parked call are other than the arguments
    it ``parked`` with: the one rule for "the operator edited this call". The
    gate stamps ``bus.EDITED_KEY`` by it (``driver.AgentSession._park``), and
    every pane and warning that says a draft is an edit asks it
    (``edited_provenance``, ``ui.inbox.draft_is_edit``).

    Compared as canonical JSON, keys sorted and no whitespace, since JSON is
    what the CLI is handed as ``updatedInput``: key order and layout are not
    an edit, and a change of type is -- ``1`` to ``true`` or to ``1.0`` is
    sent changed, where Python's equality would call each the same value.
    """
    return _canonical(sent) != _canonical(parked)


def edited_provenance(
    tool_name: str, original: Mapping[str, Any], draft: Mapping[str, Any]
) -> str | None:
    """
    What approving the operator's ``draft`` of a declaration does to the
    provenance the agent's ``original`` arguments state, as one warn line: None
    when the board will keep what the original states, and for every other call.

    Computed as the gate and the bus will compute it, so the line cannot say
    something the board then contradicts. The draft is an edit by ``is_edit``,
    the rule the gate stamps by: a draft only reformatted is not stamped as
    edited, and nothing is dropped. What an edit
    keeps is ``model.kept_provenance`` of what the draft states -- the bus's own
    rule -- which drops a charter mark the draft states whoever wrote it, the
    agent or the operator. A draft with no channel keeps no provenance at all,
    and a draft the bus refuses puts nothing on the board. An original the bus
    refuses is told apart from one that states nothing: a draft that drops its
    channel is then placed with no provenance, where the original would not
    have been placed at all, and the line says so.

    ``original`` is the call as the agent made it (``PendingApproval.raw_args``,
    or its JSON round trip), which is what the gate compares a draft with.
    """
    if not states_provenance(tool_name) or not is_edit(draft, original):
        return None
    stated = _provenance_read(draft)
    if isinstance(stated, _Refused):
        return (
            "approving your edit puts nothing on the board: the bus refuses its provenance: "
            f"{stated.reason}"
        )
    kept = kept_provenance(stated, edited=True)
    drawn = _provenance_read(original)
    if kept == drawn and kept == stated:
        return None
    if kept is None:
        match drawn:
            case _Refused():
                # DETAIL draws the original's refusal (``declared_provenance``),
                # which would otherwise read as this call's fate.
                return (
                    "approving your edit places the declaration with no provenance: the "
                    "draft names no channel, so the one the bus refuses is not sent"
                )
            case Provenance() | None:
                return (
                    "approving your edit removes the provenance: the board keeps no channel, "
                    "message or charter mark for this task"
                )
            case _:
                assert_never(drawn)
    # The mark the operator has in view: the draft's own, or else the one drawn.
    mark: Charter
    if stated is not None and stated.inside_charter is not Charter.UNSTATED:
        mark = stated.inside_charter
    else:
        match drawn:
            case Provenance():
                mark = drawn.inside_charter
            case _Refused() | None:
                mark = Charter.UNSTATED
            case _:
                assert_never(drawn)
    if mark is not Charter.UNSTATED:
        return (
            f"approving your edit drops the charter mark ({mark.value}): the board keeps "
            f"{kept.said()}"
        )
    return f"approving your edit changes the provenance: the board keeps {kept.said()}"


def declared_provenance(tool_name: str, tool_input: Mapping[str, Any]) -> str | None:
    """
    Where a parked declaration says its request came from, as one line
    (``model.Provenance.said``): None for any other call, and for a declaration
    that gives no provenance, which is shown exactly as one was before
    provenance existed.

    The detail pane's line. The queue row says the same provenance shorter
    (``summarize``), from the same reading. Arguments that cannot be read as a
    provenance say so, with the reason the handler refuses them for
    (``model.stated_provenance``, which both apply): approving that call puts
    nothing on the board.
    """
    if not states_provenance(tool_name):
        return None
    asked = _provenance_read(tool_input)
    if isinstance(asked, _Refused):
        return f"provenance the bus refuses: {asked.reason}"
    return None if asked is None else asked.said()
