# A session can be resumed and bookmarked, and the transcripts on disk say which ones exist

**Dated:** 2026-08-31 · **Status:** built and green — `pptmstr/sessions.py`,
`pptmstr/driver.py`, `pptmstr/ui/launcher.py`, `pptmstr/app.py` and their tests ·
**Supersedes in part:**
[`2026-08-22-session-controls-and-the-mode-dial.md`](2026-08-22-session-controls-and-the-mode-dial.md)
D8 — its attribution finding is reversed, its blocking unknown is routed around ·
**Found by:** operator request — "find and resume sessions from yesterday"

The launcher grows a resume picker. Picking a row sets `resume` on the `LaunchSpec`;
picking nothing launches fresh, exactly as before. The driver passes `resume=` to the
CLI and holds it to the id it was given. Bookmarks and operator titles live in a small
local overlay. Every session this application starts from now on tags its own
transcript.

This record is about work that is built, so the decisions below are stated as what the
code does and why it does it, not as what it should. Where a decision reverses a
recorded one, the reversal is argued rather than assumed.

---

## D1 — `list_sessions()` is the source of truth, and pptmstr writes no index

The picker enumerates the CLI's own transcript files through
`claude_agent_sdk.list_sessions`. It does not maintain a list of sessions it started.

**The reason is the feature's own failure case.** An index written by this application
has the same failure mode as this application: if pptmstr dies mid-session, its entry
is missing or stale — and a session lost to pptmstr dying is precisely the session the
operator is trying to get back. Transcripts are written by the CLI, they survive the
process that asked for them, and they include sessions that predate this feature
entirely. `sessions.py`'s module docstring carries the argument at the point of use so
the next reader does not optimise it away.

The local overlay at `config_dir()/sessions.json` (`sessions.overlay_path`) carries only
what `list_sessions` cannot know: a bookmark flag and an operator-supplied title, keyed
by session id. It is additive by construction — an overlay entry is never evidence that
a session exists, only decoration for one that does. Persistence copies `settings.py`
exactly rather than inventing a second convention, and a corrupt overlay degrades to
empty per-entry rather than condemning the file, because losing a bookmark is a nuisance
next to not being offered the session at all.

### This reverses D8, and the reversal is the point

D8 wrote off exactly this design:

> `list_sessions` cannot distinguish this application's sessions from the operator's own
> Claude Code sessions in the same repo; `SDKSessionInfo` has no producer field. **A
> picker built on it renders the problem rather than solving it.**

The observation is correct and still holds — nothing here can tell a pptmstr session
from one the operator started at a terminal. The inference from it is what this record
disagrees with, on two grounds.

**First, the unattributed sessions are not noise; they are half the ask.** The operator
loses their own Claude Code sessions in this repo too, and a picker that showed only
pptmstr's would not have answered the request that started this work. Mixing them is not
a defect of the source, it is the source being wider than the problem statement.

**Second, the alternative D8 implies — an index we maintain, which would be attributable
— is unavailable for the case that matters.** Attribution bought that way is bought by
spending survivability, and survivability is the whole feature. D8 named attribution as
the cost of using `list_sessions` without pricing what the alternative costs.

So the design takes the imperfect source and refuses to pretend otherwise. `tag` is
exposed for a caller to mark what it recognises, and **nothing is filtered on it.**

## D2 — Resume verifies the session id; it does not adopt one

On resume, `AgentSession.__init__` sets `self.session_id = resume` and `_options()`
passes `resume=` with `session_id` omitted entirely. The CLI is never handed two
candidate ids. The `init` frame and every `ResultMessage` are then *compared* against the
id we already hold (`_check_effective_id`), and a mismatch raises `SessionIdentityError`
and ends the session. A resumed session that reaches the end of its stream without the
CLI ever naming an id fails too — "we could not check" and "we checked and it was fine"
are different answers.

### The design specified first was wrong, and the reason it was wrong is the useful part

The original `driver-resume` spec said: pass `resume=`, read the effective id off the
`init` frame, and **adopt** it as `self.session_id` "before anything emits a node". A
builder stopped on it before writing code and held the claim rather than releasing it,
on the grounds that releasing would republish a spec it believed produced a broken
session:

> The spec says "adopt the effective id at the `init` frame, **before anything emits a
> node**". A node is already emitted before the driver has a subprocess.

`SessionPool.submit` does this, and `app._launch` calls it:

```python
self.sessions[session.node_id] = session
session.announce()
if len(self._running) < self.cap:
    self._start(session)
```

`_start` then keys `self._running[session.node_id]`. So by the time `AgentSession.run()`
is entered — the earliest moment any `init` frame can exist — the id has already been
emitted as an `AgentSpawned` and used as the key in **two** pool dictionaries.
`app._seed_brief` has also already derived the brief directory from it.

Adoption at `init` therefore rehomes the session onto an id that three earlier writes do
not use, and every one of the four resulting failures is silent:

1. **`send` is a no-op.** `SessionPool.send` looks up `self.sessions[node_id]` under the
   announced id; the session is filed there but no longer answers to it, and a typed
   message goes nowhere with no error.
2. **`interrupt` is a no-op**, by the same lookup. The one control an operator reaches
   for when a session is misbehaving is the one that stops working.
3. **`close` does not cancel.** The task lives in `_running` under the announced id, so
   the cancel misses and the subprocess keeps running.
4. **The pool slot leaks and the store grows a second root row.** `_run`'s `finally`
   does `self._running.pop(session.node_id, None)` — after adoption that pops the *new*
   key, the old one is never removed, and capacity is permanently reduced. Meanwhile
   `announce()` has already emitted `AgentSpawned` under the minted id, so intents under
   the adopted id build a second root row and the first is stuck in SPAWNING forever.

None of the four raises. All four look like "the session is a bit odd today".

Verifying instead of adopting makes the class unreachable rather than handled: the id is
settled at construction, which is the moment `submit`, `announce` and `_seed_brief` all
read it. `SessionIdentityError`'s docstring records why the disagreement is fatal rather
than absorbed — "a session that stopped with an error the operator can read is a far
cheaper outcome than a live one whose `send`, `interrupt` and `close` all silently miss."

**The `init` frame is read here and refused elsewhere, and that is consistent.** This
codebase distrusts `init` because it echoes argv back. That objection is about
arbitration; a comparison whose entire purpose is to catch the CLI disagreeing with the
one id we passed it is not arbitration. `ResultMessage.session_id` is the authoritative
reading and arrives after the CLI has settled; `init` is worth having only because it
arrives at the handshake rather than a whole turn later.

## D3 — D8's blocking unknown is moot, not answered

D8 named one unreadable blocker: **which session id the CLI uses when `--session-id`,
`--resume` and `--fork-session` are passed together**, and routed the design on it —
supplied id honoured means resume keeps NodeId stability and is a small change; not
honoured means the fork must happen before the session is constructed.

**This design never produces that combination.** It passes `resume` alone, with
`session_id` omitted and `fork_session` unset. Nothing arbitrates because nothing
competes.

And the shape it does produce is documented rather than inferred. `ClaudeAgentOptions`
in SDK 0.2.134 says of `fork_session`:

> When true, resumed sessions fork to a new session ID rather than continuing the
> previous session.

A fork to a new id is what `fork_session=True` is *for*. Bare `resume` continuing the
previous id is the SDK's stated contract, not an assumption — and `_check_effective_id`
holds the CLI to it at runtime instead of trusting the docstring, which is what makes
relying on it safe.

### The probe exists, has not been run, and must not be quoted

`scripts/verify_resume_session_id.py` was built for D8's question and reviewed. **It has
never been executed, and it is deliberately not part of this feature.** Saying so here is
the point of the section: an unexecuted probe script sitting in the tree reads as
evidence, and this one is not.

`probe-review` found six defects in it, **three of which produce a confidently wrong
verdict string**. The reviewer's summary of where they are:

> the id readings are sound and the "request not result" defect is NOT present in the Q1
> path […] The defects are all in the *failure and disagreement* arms, which is exactly
> where the builder said it had not looked.

The builder had already flagged the same region unexercised before review — the
STALLED/REJECTED split "replaced the single REJECTED branch that I *had* confirmed
reachable, and **neither replacement arm has been exercised by anything**."

One judgement inside that work is worth keeping, because it will otherwise be
re-proposed. A hazard list recommended `ClaudeAgentOptions.tools=[]` as a "stronger belt"
for the probe's canary arm. The builder refused it and was right to:
`subprocess_cli.py` turns `tools=[]` into `--tools ""`, an argv shape nothing in this
repo has run, whose failure mode is a CLI-level error — and REJECTED is a verdict the
probe legitimately produces. An untested flag whose failure is indistinguishable from a
real outcome sits directly on top of that outcome, and a spurious REJECTED would have
told D8 "the CLI refuses this combination" when the CLI had refused the probe's own flag.

The gate that signed off on this file checked formatting only. `make typecheck` is
`mypy pptmstr`; `scripts/` is covered by `typecheck-all`, which `check` deliberately
excludes. Lint-clean, in this file's case, is a much weaker claim than green implies.

## D4 — Tagging fires at the first completed turn, not the first message

`SESSION_TAG = "pptmstr"` is written into the session's own transcript by
`_start_tagging`, triggered on the first `ResultMessage`.

D8 said the tag "must fire after the first message because it refuses a zero-byte file."
That is close and not quite right, and the difference decides the trigger.
`session_mutations.py` opens with `O_WRONLY | O_APPEND` and **no `O_CREAT`**: a zero-byte
file appends fine; a *missing* one raises `FileNotFoundError`. At the first message the
file's existence is a race with the CLI's first flush. At the end of a turn it is
certain.

The write is detached rather than awaited. `tag_session` is synchronous file I/O that can
reach a `git worktree` subprocess on its fallback path, and the event loop it would block
is the only thing servicing the approval gate and every hook callback for the session.
Awaiting the thread hop inline puts a suspension point on the critical path of message
handling where there is none today — a latency cost on every session and a change in the
loop's observable ordering. `_finish_tagging` bounds the detached task to the session's
lifetime so it cannot outlive the loop and be destroyed mid-write.

**The cost, accepted:** a session that never completes a turn is never tagged. This is
affordable only because the tag is a marker and never a filter — an untagged session is
still listed and still resumable. If anything ever filters on `tag`, this trigger becomes
a defect and has to be revisited with it.

## D5 — No "recognised by pptmstr" affordance shipped

The tag is written and exposed on `SessionRow`, and the launcher does not draw anything
from it.

On 2026-08-31 the transcript tree holds 169 sessions and **`tag` is `None` on every one
of them** (`builder-3` measured 167, all `None`, on 2026-08-30; the count moves, the
answer does not). A "recognised by pptmstr" marker would therefore be dark for
everything that exists today and would light up only for sessions started from now on.
A badge that is off for the entire visible list does not read as "unattributable" — it
reads as broken, or worse, as a real distinction the operator starts trusting before it
means anything.

The affordance becomes worth building once tagged sessions are a meaningful share of the
list. Recorded so the absence reads as a decision rather than an oversight.

## D6 — A resumed team session seeds its brief into the original session's brief

`_seed_brief` runs between construction and `pool.submit`, and derives its directory from
`brief.session_dir(root, cwd, session_id)`. Because a resumed session runs under the id
it is resuming, that directory is the **original session's** brief directory. So resuming
a team session with the launcher's brief field left empty appends the launch text as a
new premise into the brief the original session was writing.

This is left as built, deliberately.

`brief.write_entry` is append-only — entries are added and never edited, which is the
brief format's own contract and the reason a reader is told a later entry may supersede
an earlier one. Nothing is overwritten and nothing is lost. What the resumed session's
workers get is the original premises plus one more saying what this launch was for, which
is a truthful account of the session's history and is very close to what the operator
would have written by hand.

`_seed_brief`'s docstring says seeding is skipped "when the operator named a directory —
that is a session pointed at an existing brief, typically a fork inheriting its parent's,
and seeding over it would bury the premises it was launched to continue." **That
reasoning was written about forks and does not transfer to resumes.** A fork gets a *new*
id and so derives an *empty* directory; pointing it at its parent's brief is an explicit
act, and seeding over that pointer would put a launch note ahead of premises the fork
never wrote. A resume derives the directory it already owns, and appending to your own
brief is not burying anything. The launcher's "use this session's brief" button fills the
field explicitly and suppresses the seed, which remains available for an operator who
wants the original premises untouched.

This is a property of D2 rather than an accident of it: `brief.session_dir` is a function
of cwd and session id, so checking the id rather than adopting one is what makes the
premises reachable at all. A session rehomed onto a new id would derive an empty
directory and silently lose them.

---

## What resume does not restore, and where the UI says so

D8's other four constraints are unaffected by anything above and still hold.

**A resumed session has no `AgentRecord`.** There is no record on the other side of the
CLI; `task`, `model` and `template` are whatever this launch supplied and are not what
the original ran with. The word "resume" promises more than the mechanism delivers, so
the picker says it in as many words — "model, team and task are this launch's — they are
not read back from the session". The brief is the one exception, and only because the id
does not move (D6).

**`list_sessions` is synchronous blocking I/O** — per-file open, stat and head/tail reads
over every transcript plus a `git worktree` subprocess. `enumerate_sessions` is the one
name all of it lives behind; the launcher runs it on a worker thread and caches merged
rows, and `sessions.py` starts no threads of its own. A bookmark toggle rewrites the
single row it changed rather than re-merging, which is cheaper and leaves the order the
operator is reading alone.

**Attribution is imperfect**, and D1 records what was done about it instead.

**Three things are still called fork** — `ClaudeAgentOptions.fork_session`, the
`fork_session()` function, and this application's fork button. Resume sits beside the
button and does not repair it. One of the three still has to be renamed; this work did
not do it.

Two smaller things the build surfaced and did not resolve:

- `enumerate_sessions` refuses to turn a listing error into an empty picker, but
  `same_cwd` is an exact string match over absolute paths while `LauncherState.cwd`
  defaults to `"."`. That route reaches an empty picker without an error, and reads as
  "you have no sessions" when it is a path-shape mismatch. Both docstrings now say so;
  collapsing the two filters would hide that they have different jobs.
- The scan fires on modal open against the cwd field as it then stood, so editing cwd
  and *then* expanding the picker lists the previous directory. The caption naming the
  directory actually searched is the existing mitigation; the fix is tracked separately.

## Verification boundary

**Run:** `make check` — black, ruff, mypy and the full suite — green at the feature's
terminal gate. `pptmstr/sessions.py` has 41 tests, mutation-tested at 12 mutations with
one survivor found and closed; `tests/test_launcher.py` has 50. The launcher was also
driven in the real application against the real transcript tree (129 sessions in this
repo at the time), not only in tests. `list_sessions`' field list, optionality and
non-coroutine-ness, and `fork_session`'s docstring, were checked against installed SDK
0.2.134.

**Not run:** `scripts/verify_resume_session_id.py`, ever — see D3. No live resume of a
session across a pptmstr restart was measured; the identity check is exercised by unit
tests against synthetic frames, not against a CLI that disagreed with us.

**Read, not run:** every `pool.py`, `app.py` and `session_mutations.py` citation above.

**Units, because it will bite the next caller:** `SessionRow.created_at` and
`last_modified` are epoch **milliseconds** — the SDK builds them as
`int(st_mtime * 1000)`. A caller comparing them to `time.time()` without dividing gets an
age about fifty thousand years off, which looks like corrupt data rather than a units
bug. It is documented on the dataclass and nothing enforces it at the boundary.

## Records this one corrects

- [`2026-08-22-session-controls-and-the-mode-dial.md`](2026-08-22-session-controls-and-the-mode-dial.md)
  **D8** — its attribution constraint is reversed (D1 above), its blocking unknown is
  routed around rather than answered (D3), and its `tag_session` trigger is stated for
  the wrong reason (D4). Its other four constraints stand. Its sequencing — "Probe 2,
  then resume" — was not followed, and D3 is the argument for why the probe stopped being
  on the critical path.
- [`2026-08-22-four-items-buy-back-session-time.md`](2026-08-22-four-items-buy-back-session-time.md)
  and
  [`2026-08-21-a-lead-is-already-free-while-its-workers-run.md`](2026-08-21-a-lead-is-already-free-while-its-workers-run.md)
  both cite a splash test that no longer exists. Corrected in place; the finding is
  `builder-7`'s, made while trying to confirm a failure it had been told to expect.
