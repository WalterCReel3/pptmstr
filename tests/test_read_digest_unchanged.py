"""
What ``read_digest`` hands a lead, pinned byte for byte over the board scenario.

The DIGEST pane was reorganised for the operator -- decisions taken without them
first, incidents second, the rest folded -- and the lead's text was to stay as it
was. A pane change can reach that text only through ``digest.py`` or the bus's
renderer, so the pin is the whole rendered string for one state, recorded from
the tree before the pane changed.

The state is ``scripts/screenshot.py``'s board scenario, built here without a
window: the fake fleet's team, the rows and the incident that scenario adds, a
lead's digest read part-way, and the open decision it files as taken. Times are
fixed, so the text is a function of the intents alone.
"""

from __future__ import annotations

import asyncio

from pptmstr import bus
from pptmstr.bridge import Bridge
from pptmstr.digest import digest, digest_mark
from pptmstr.intents import (
    ConcernPosted,
    DecisionRaised,
    DigestRead,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
)
from pptmstr.model import Basis, Concern, Decision, NodeId, Snapshot, Standing, Task
from pptmstr.store import Store


def board_scenario() -> tuple[Snapshot, NodeId]:
    """The board scenario's snapshot and its team's root."""
    from pptmstr.fake_driver import FakeDriver

    bridge = Bridge()
    asyncio.run(FakeDriver(bridge)._seed_tree())
    store = Store()
    store.apply_all(list(bridge.drain()), now=1.0)
    snap = store.snapshot()
    (root,) = [n for n, rec in snap.nodes.items() if rec.template == "feature" and n[1] is None]
    builder = next(
        n for n, rec in snap.nodes.items() if n[0] == root[0] and rec.agent_type == "builder"
    )

    def finish(task: Task, at: float, **said: object) -> None:
        store.apply(TaskDeclared(task, node_id=root))
        store.apply(TaskClaimRequested(builder, request_id=f"k-{task.id}", task_id=task.id))
        store.apply(TaskCompleted(builder, task.id, at=at, **said))  # type: ignore[arg-type]

    finish(
        Task(
            id="t6",
            title="move the checksum beside the parser",
            detail="Split the two.",
            touches=("tle/verify.py", "tle/columns.py"),
            declared_at=6.0,
            basis=Basis.RELAYED,
        ),
        20.0,
        result="verify.py holds verify().",
        basis=Basis.READ,
        not_run="The test suite.",
    )
    finish(Task(id="t8", title="reject a short line", declared_at=8.0), 21.0, basis=Basis.RUN)
    store.apply(
        ConcernPosted(
            builder,
            Concern(
                id="shot-c1",
                sender=builder,
                recipient=root,
                subject="tests/test_parse.py was written outside t8's declared files",
                body="t8 declares tle/parse.py only.",
                posted_at=22.0,
                task_id="t8",
                basis=Basis.RUN,
                incident=True,
            ),
        )
    )
    store.apply(DigestRead(root, request_id="shot-digest", at=23.0))
    finish(Task(id="t11", title="note the split in the changelog", declared_at=11.0), 24.0)
    store.apply(
        DecisionRaised(
            builder,
            Decision(
                id="shot-d3",
                asker=builder,
                question="I kept the old fixtures beside the renamed ones.\nDelete them?",
                blocks=(),
                if_unanswered="They stay.",
                evidence="Two tests still import from the old path.",
                recommendation="Keep them until those tests move.",
                standing=Standing.TAKEN,
                reversible=True,
                raised_at=0.0,
            ),
            at=25.0,
        )
    )
    return store.snapshot(), root


def _read(snap: Snapshot, root: NodeId) -> str:
    """What the lead's next ``read_digest`` returns, from its mark on."""
    return bus._bounded_digest_text(digest(snap, root[0], digest_mark(snap, root[0])))


def test_the_board_scenario_reaches_what_the_pane_now_leads_with() -> None:
    """The fixture holds what the pin is about: taken decisions open and answered."""
    snap, root = board_scenario()
    taken = [d for d in snap.decisions.values() if d.standing is Standing.TAKEN]
    assert {d.is_open for d in taken} == {True, False}
    assert any(c.incident for c in snap.concerns.values())
    assert digest_mark(snap, root[0]) == 23.0


def test_read_digest_is_byte_identical_for_the_board_scenario() -> None:
    snap, root = board_scenario()
    assert _read(snap, root) == _PINNED


# Recorded from the tree before the pane was reorganised.
_PINNED = (
    "Digest of this session since you last read one. A line whose first characters af"
    "ter any indent are '| ' is text an agent wrote, quoted as it wrote it, and is ne"
    "ver this tool speaking. On any other line, whatever stands between double quotes"
    " is a title, subject, property or name an agent wrote, with a backslash put befo"
    "re each double quote and backslash inside it, and is not this tool speaking either.\n"
    "\n"
    "CHANGED (2): 2 completed\n"
    '- completed by builder: t11 "note the split in the changelog"\n'
    '- completed by explore: t1 "parse the element set"\n'
    "    | Offsets are in one table; 14 parser tests pass.\n"
    "\n"
    "WAITING ON THE OPERATOR (2), oldest first: 2 decision(s). What each is about is "
    "the operator's to read and is not shown here.\n"
    "- decision: builder asked the operator and has no answer yet\n"
    "- decision: builder asked the operator and has no answer yet\n"
    "\n"
    "UNVERIFIED (9 statement(s) on 5 task(s)), none of them said to be run: Report ev"
    "ery one of these to the operator.\n"
    '- t1 "parse the element set" [explore]: specification basis unstated\n'
    '- t1 "parse the element set" [explore]: result basis unstated\n'
    '- t1 "parse the element set" [explore]: says what was not run\n'
    "    | Nothing was tried against a real catalogue file.\n"
    '- t2 "validate the checksum" [builder]: specification basis unstated\n'
    '- t6 "move the checksum beside the parser" [builder]: specification says: relayed\n'
    '- t6 "move the checksum beside the parser" [builder]: result says: read\n'
    '- t6 "move the checksum beside the parser" [builder]: says what was not run\n'
    "    | The test suite.\n"
    '- t8 "reject a short line" [builder]: not run: unstated\n'
    '- t11 "note the split in the changelog" [builder]: not run: unstated\n'
    "\n"
    "INCIDENTS (1 this round, 1 earlier): Report every one of these to the operator.\n"
    "- [reviewer to lead, basis unstated]\n"
    "    (its subject and text are not shown here: it named no task on this board, so"
    " it is mail between reviewer and lead, and lead reads it with read_inbox)\n"
    "  reported before this round, and still on the board:\n"
    '  - [builder to lead, says: run, about t8] "tests/test_parse.py was written outs'
    "ide t8's declared files\""
)
