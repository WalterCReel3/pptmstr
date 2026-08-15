# Style

How this codebase is written, and why. Every rule below is anchored to something
already in the repository — if a rule stops matching the code, one of the two is
wrong and it is worth finding out which before writing more.

`CLAUDE.md` covers how to work here. This covers how the code is shaped.

---

## 1. A functional core, an imperative shell

The store, the records and the reducer are written in an ML-ish style: sum types,
immutable values, exhaustive `match`, and state transitions as pure functions. The
threading, the IO and the UI are ordinary imperative Python. The boundary is
deliberate and it is worth being able to point at.

### Sum types, not base classes

`Intent`, `Obligation` and `Effect` are explicit unions of frozen dataclasses:

```python
Intent = AgentSpawned | StateChanged | ... | TaskReleased
```

A base class with subclasses would express the same shape and lose the property
that matters: `match` over a union plus `assert_never` in the default arm makes an
unhandled variant a **type error at a specific line**, not a silently ignored state
change. Adding a variant and forgetting to handle it is the defect this buys
protection from, and it is a miserable one to find from the UI.

So: new variant → add it to the union, run mypy, fix what it points at.

### Records are frozen; you build a replacement

`@dataclass(frozen=True, slots=True)` throughout `model.py`. Mutation is
`rec.with_(...)` or `dataclasses.replace`. This is what makes `Store.snapshot()` an
attribute read rather than a deep copy (I3), which is what makes a per-frame rebuild
affordable in Python at all.

### The reducer is pure and total

```python
def _apply(snap: Snapshot, intent: Intent, now: float) -> tuple[Snapshot, tuple[Effect, ...]]
```

A free function, not a method, so it can be exercised without a `Store`. Every arm
either produces a new snapshot or returns the old one unchanged; nothing raises,
nothing reads a clock, nothing performs IO. `now` is a parameter precisely so the
core cannot reach for `time.monotonic()` mid-batch.

An intent for something that does not exist is a **no-op, not an error**. Unknown
node, already-settled approval, double click — all normal, all `return snap, ()`.

### Derive; do not store

If a fact follows from other facts in the same snapshot, compute it on read.

- `TaskState` has **no `BLOCKED` member.** Blocked-ness is a function of the
  dependency graph (`Task.is_claimable`), so "automatic unblocking on completion"
  has no unblocking step — there is nothing to forget to run.
- `Snapshot.needs_you` is one projection over three obligation kinds, not three
  lists that agree by convention. It exists *because* "waiting on you" previously
  had two implementations and one of them had no surface at all.
- `ContextSnapshot.pressure` is computed, not stored.

A stored duplicate of a derivable fact is kept true by whoever remembers to update
it, and "whoever remembers" is where this codebase's defects have historically come
from. When you catch yourself adding a field that another field implies, stop.

### When the core must answer, widen the return

Two bus tools ask *questions* — `claim_task` and `read_inbox` — and a reducer that
could only return the next world had nowhere to put a reply.

The first attempt hid the answer inside the world: the winning `Task` carried the
`claim_id` that took it, and the shell scanned the new snapshot for its own request
id. That worked, and it was wrong. It put a **transport correlation token into a
domain record**, and it obliged `TaskReleased` to clear the token or a later request
reusing the id would be answered by a stale record.

The fix was to return `(Snapshot, tuple[Effect, ...])` — the same tagged-union shape
as `Intent`, pointing the other way. Two hazards stopped being possible rather than
being handled: no token in the domain, and an effect exists *only* because an intent
was applied, so the shell can never answer a request whose intent is still in the
queue.

**Rule:** if the pure core needs to tell the shell something, return it. Do not
route it through the state.

### Where not to do this

Being functional in the wrong place costs more than it buys, so these are
deliberate exceptions, and each one has a reason attached in its own module:

| | Why it stays imperative |
|---|---|
| `Store` | A mutable cell holding one reference. Its whole job is to be the identity that gets swapped (I3). |
| `Transcript` | Append-only and internally synchronised (I7). Routing a token stream through the reducer would put one queue item per token on the frame path. |
| `Bridge` | It *is* the thread boundary. Purity here would fight the threading model I5 exists to protect. |

No `Result`/`Either`. The core is not failure-heavy, and exceptions at the boundary
are fine.

---

## 2. Verification

### "Plumbed through" and "works end to end" are different claims

Only the first is provable by reading. Every claim in the design docs that rests on
runtime behaviour has a script in `scripts/` that produced it, and the doc cites the
script and the CLI version.

Reading the SDK established that an MCP handler receives only a tool name and
arguments. It could **not** establish that a `PreToolUse` `updatedInput` rewrite
survives the hop out to the CLI and back into an in-process server — a different
claim, and the one the whole message bus depended on. That needed a run.

### A probe must capture the result, not the narration

The first wake-path run recorded that a worker said `"sent"` and concluded the
message was delivered. It had been **refused** — `"No agent named 'alpha' is
reachable."` A model reporting success is not evidence of success. Capture the tool
result, the hook input, the wire.

### Mutation-test new tests

A new test file passing 28/28 on the first run is a smell, not a result. Break the
behaviour deliberately and confirm the test fails. Every non-obvious guarantee in
this repo has had this done to it, and it has caught real gaps.

### Test the wiring, not only the unit

Unwiring `_check_for_stranded_requests` from the frame loop left **every one of its
unit tests passing**. A watchdog nothing calls is worse than no watchdog, because it
reads as covered. Where a pure function's value is entirely in being called, assert
that it is called.

### Do not claim more than you verified — including in your own tooling

`verify_bus_live.py` printed *"the claim round-tripped through the effect channel
and the agent was told what it won"* while checking only that the board said
CLAIMED. The board is set at apply time whether or not the reply ever reached the
agent, so the line asserted something it never tested. A verdict must be computed
from the thing it names.

### A test's name is a claim; check the body makes it

Both defects that survived the board pane were tests asserting less than their names
promised, and both were at the gate. `Concern.edited` had a reducer arm covered by
unit tests that applied `ConcernEdited` directly, while **nothing in `pptmstr/` ever
emitted it** — the operator's rewrite reached the handler as ordinary arguments and
built a fresh record with the flag defaulted off, so the store kept the edited text
and no trace that it was edited. Its replacement,
`test_the_edit_stamp_is_not_something_a_model_can_set`, then passed a tool input that
never contained `_edited` and asserted the key was absent afterwards — true of the
defective code too, since the stamp is a copy of the model's own arguments and the
only thing worth asserting was that a value *already there* gets overwritten.

The gate is where this keeps happening because it is the one component whose job is
to know something none of its neighbours can — who called, and whether the operator
intervened. A test that does not supply the adversarial input cannot tell a gate that
establishes a fact from one that merely passes it along. So: name the test after the
hazard, then check the body actually constructs it, and mutation-test against the
**current** line rather than a broken variant invented for the purpose — the invented
one is chosen to fail.

---

## 3. Smells, with their instances

**A comment defending against a hazard.** If you are writing "remember to clear
this or X breaks", the design is wrong, not the comment. `TaskReleased` had to null
`claim_id`; deleting the token deleted the comment.

**A default where the honest answer is a refusal.** An unstamped bus call raises
`UnstampedCall` rather than guessing a sender. A default would silently attribute
someone's message to a plausible-looking node, which is worse than a crash.

**A duplicated constant with no test pinning it.** `approval.py` spells the bus
server name rather than importing it, so the policy stays free of SDK imports. That
duplication is deliberate *and* pinned by a test that fails if the two lists drift.
Deliberate duplication is fine; unpinned duplication is not.

**An error message that does not distinguish the two mistakes it covers.** "No such
agent" covered both a misspelled role and a role that simply had not started yet —
one is a spelling problem, the other a timing problem the lead can fix. A worker
retried the same wrong name because the refusal did not say which it had made.

**A channel that duplicates a path the model already has.** The first team run used
the message bus zero times, because a sub-agent's result already returns to its lead
through the `Agent` tool. Listing a tool in a prompt does not make it used; saying
what it is *for* — the thing the other path cannot carry — does.

---

## 4. Comments and docstrings

Covered in `CLAUDE.md` and worth restating in one line, because it is the rule most
often broken here: **comments describe the code as it is, not as it was.** The
distinction is tense, not subject matter. A live constraint earns a comment; the
history of a defect does not.

The exception is a decision whose *reasoning* is not visible from the result. When
a module says why it takes the harder of two reasonable paths — why `approval.py`
has no SDK import, why the bus handler refuses instead of defaulting — that is not
archaeology, it is the constraint that will otherwise be optimised away by the next
reader.

Docstring triple quotes go on their own line.
