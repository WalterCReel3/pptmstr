# The task board and the conversation are in the store and on no screen

**Dated:** 2026-08-12 · **Status:** built 2026-08-15, `c1f775e`, in DETAIL as this
doc directed. It moved to a pane of its own on 2026-08-18
([`2026-08-18-the-board-is-a-tenant-of-a-pane-that-owes-it-nothing.md`](2026-08-18-the-board-is-a-tenant-of-a-pane-that-owes-it-nothing.md),
`7ccb745`), which is where the placement argued for below was reversed ·
**Follows:** step 8 landing (`2026-08-12-a-message-has-no-sender-until-the-gate-gives-it-one.md`)

`Snapshot.tasks` and `Snapshot.concerns` have **zero readers in `pptmstr/ui/`**.
Checked, not assumed:

```sh
grep -rn "\.tasks\b\|\.concerns\b\|inbox_of\|claimable_tasks" pptmstr/ui/   # no matches
```

So a team session today is half-observable. What the operator can see:

- roles appearing as sub-agent rows as the lead spawns them, through the tree that
  already existed;
- every `post_concern` parked in the review queue as an ordinary approval reading
  `message skeptic: <subject>`, readable, rejectable with a reason, and editable
  before delivery.

What they cannot see: **who has claimed what, what is blocked on what, and anything
a worker was told earlier.** All of it is in the store, snapshot every frame, and
drawn by nothing.

That asymmetry is not neutral. The operator is being asked to approve a message
between two agents without being able to see the work either of them holds, which
is most of the context that would make the decision easy.

## Where it goes

The **detail pane**, for a selected team session. Not a new panel.

`2026-08-10-layout-proposals.md` settles this by its own rule: the inbox row is
where you *act*, DETAIL is what *informs* the act, and a team's board is exactly
the thing that informs whether a concern is worth delivering. A new panel would
also need a place in both arrangements, and FOCUS is already recorded there as
vestigial — adding a pane to a layout nobody uses is how the last one got that way.

Rough shape, in the order it earns its space:

| | |
|---|---|
| tasks | id, state, owner *role* (not agent id), and what it is blocked on |
| concerns | sender role → recipient role, subject, delivered or waiting |

Two details already decided by the store: a task's owner renders through
`AgentSession.role_of`, since `(session_id, agent_id)` means nothing to a reader;
and blocked-ness is derived from the graph on read, so the pane must not cache it.

## Open, and worth settling before drawing

- **Does the board belong to a session or to the fleet?** It is session-scoped in
  the store — one bus per `AgentSession` — which argues for the detail pane. But the
  pool spans projects, and "what is every team doing" is a fleet question. Starting
  session-scoped is the smaller commitment and does not foreclose the other.
- **What a solo session shows.** Most sessions have an empty board forever. The
  pane must not grow two empty headings for every non-team session; likely the
  section only appears when the session has a template, which the record already
  knows.
- **Whether a delivered concern stays visible.** The store keeps it, and it is the
  only record of what a worker was actually told — including the operator's edit. A
  pane that shows only what is waiting throws away the audit trail that made
  concerns store objects in the first place.

## Why not now

Step 8 was scoped to the mechanism and stopped when the mechanism was verified. The
§9 requirement to "close the gating question before the pane" is already satisfied,
and satisfied by deletion: gating the send made concerns ordinary approvals, so
there is no concern *review* pane to design. What is left is a **conversation and
board view**, which is a rendering question rather than an interaction one — and
rendering questions in this repo are the ones that iterate, so they are worth
starting with a real run's data on screen rather than a fixture's.
