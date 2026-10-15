# Session control and the inter-team record, in one sequence

**Dated:** 2026-10-12 · **Status:** partly built on branch `enhanced-sessions`, uncommitted;
ten operator questions open. A second run the same day reviewed, measured live and fixed what
the first could not: its results are in "Second run" at the end, and where the two disagree
the second run is the later evidence · **Found by:**
operator request, quoted below · **Stitches:**
[`2026-10-07-a-session-can-run-in-its-own-worktree.md`](2026-10-07-a-session-can-run-in-its-own-worktree.md),
[`2026-10-07-a-session-can-continue-under-a-different-mode.md`](2026-10-07-a-session-can-continue-under-a-different-mode.md),
and two postmortems outside this repository,
`~/Source/imgui-adv-text/docs/postmortem-2026-10-adoption-and-demo.md` and
`postmortem-2026-10-swap-and-three-sessions.md` (its closing section, "For pptmstr")

This record says what one team session built from those four sources, what it decided without
the operator, what it could not run, and what waits on an answer. It was written by the
session's lead.

**Four kinds of statement, kept apart.** The first three are the sibling records' tags.

- **(D)** — the operator's words, quoted. The decision is what the words say and no more.
- **(R)** — a recommendation. Not agreed.
- **(O)** — open. No option chosen.
- **(L)** — a decision the lead or a builder took during the session because the session ran
  unattended, built into the tree, and **not ratified**. Each one is listed in "Decisions taken
  without the operator" with what overturning it costs.

**Evidence class.** "Measured" means a builder executed it in this session and reported the
output; the host was git 2.39.5, Python 3.11.2, `claude-agent-sdk` 0.2.164, bundled CLI 2.1.292
(the `claude` on `PATH` is 2.1.251 and is not what sessions spawn — measured by
`cli_version.resolve_cli_path`). "Read" means established by reading. **No live session ran**:
the session was itself sandboxed, and a write to `$HOME` and a fetch of `example.com` from a
builder's shell were both refused (measured). Nothing drawn by Dear ImGui was seen by anyone.

---

## What was asked

**D1.** "Look for the recent planning around sessions control (worktrees, expanding and
contracting permissions). Then find the postmortem documents around inter-team communication for
pptmstr in `~/Source/imgui-adv-text/docs`. I want you to stitch together a set of work to enhance
pptmstr with these features. If you have any questions, concerns or need me to run something with
elevated permissions then let me know."

**D2.** After the lead had boarded session control only: "Why in the world would you assume I
_DIDN'T_ want the interteam work? Wasn't that in the postmortem I pointed you at?"

**D3.** "I don't want apologies. I want explanations. That helps me in the future to shape prompts
and direction text."

**D4.** "I really tried to guard this with the postscript about stopping for questions. Was that
not enough? What should I say to ensure the shape of the pre-planning before automation?"

### How the request was misread, because D3 asks

The lead built the session-control half and held the inter-team half for a planning record. The
inputs that produced that, in the order they weighed:

1. Session control had dated records with a sequence; the inter-team features had none here.
   `CLAUDE.md` says to read planning records "before proposing a direction". The lead turned
   that into "no record, so write one first", which nothing says.
2. The postmortem calls its own list "suggestions ... for pptmstr's own planning to take or
   leave". The lead let the document's hedge set the scope over D1.
3. D1's first sentence says "look for the planning", its second "find the postmortem
   documents". The smaller reading was available and was taken without asking.
4. The lead's role text for the session said it "runs unattended", that "a question raised and
   left is work stopped", and that where the premises are silent "the team decides". D1's
   closing sentence is conditional on having a question; a reading already chosen does not
   register as one.

**(R) What would have caught it**, as wording for a launch: require artefacts and a stop, not
questions — the scope as a list of what will be built, **every item in the sources that will not
be built, with the reason**, the decisions the lead would otherwise take with a recommendation
each, and what needs the operator to run; then stop until answered. **(R)** In the product: the
unattended briefing (`templates._unattended_lines`) says a reduction in scope is parked at the
operator even when nothing else is, and a session's first declaration is held until a scope
statement is signed off. Neither is built.

---

## Built: session control

Every item was specified with one non-negotiable sentence, built by one builder, and read by a
reviewer who could not run code; every review finding was reproduced by a builder before it was
fixed. Four review rounds. Counts are the builders' own.

| What is now true | Where | Measured | Not run |
|---|---|---|---|
| A live widen to a rung that requires containment is refused on a session that has none; `containment` cannot be assigned after construction; an empty settings string counts as none | `driver.AgentSession._unpaired`, `set_policy` | 270 tests across driver, gate, launch policy; mutants killed incl. a name-keyed guard | any CLI |
| A relaunch under the same session id keeps its pool slot, is not announced until the run it replaces has ended, and is not marked finished by it | `pool.SessionPool` (`_retire`, `_unwinding`, `_succeeding`) | reproduced at the reducer and the pool before the fix; 43/43 mutants | a real teardown |
| A cancellation is no longer swallowed while a session abandons a read | `driver._await_subagents` | window measured with the SDK's client over a memory stream: about two loop iterations, n=3 | a real CLI |
| Shutdown returns: 25 s grace, names what has not ended, cancels those again, 5 s more; the bridge stop is in a `finally` | `pool.shutdown`, `app._shut_down` | stubs only | a wedged CLI |
| A resumed session launches in the directory its row recorded, shows that directory, and is held when it is gone; the row as clicked decides | `launcher.launch_directory`, `SessionPicker.picked` | 205 launcher tests | a live resume; the modal |
| A launch that fails after the modal closes is shown with its draft; typed text is never overwritten (restore is a swap); a restore under a focused box survives the next frame | `app._reap_launches`, `launcher.Draft`, `FailedLaunch`, `set_aside` | the focused-box rule against real Dear ImGui 1.92.8, headless, real events | anything rendered |
| The launcher states a write boundary only for the layout it was measured in; every other git layout reads "not measured" | `launcher.git_layout`, `contained_text` | layouts confirmed against real git | drawn; wrapped at 620 px |
| pptmstr can detect, place, pre-flight, create and list linked worktrees. **No caller.** | `pptmstr/worktree.py` | 107 tests against real throwaway repositories, git 2.39.5 only | any other git; macOS; a launch |
| A probe exists for the sibling record's P2 and P1 (a session resumed under containment is sandboxed) | `scripts/verify_resume_containment.py` | verdict logic: 80 tests, 41/41 mutants; `--host-only` from a sandbox prints INVALID | **live: never** |

**Measured about the code as it was, and now pinned by tests that state present behaviour:**

- A second `AgentSpawned` for a node replaces its record wholesale. A resumed root loses its
  on-screen usage, context and pending approvals; the old process's sub-agents stay as nodes; a
  task claimed by one stays `CLAIMED`, and the continued root can neither claim nor release it
  (`TaskRefusal.NOT_YOURS`). This is the sibling record's O3, answered as a measurement.
- `AgentSession.run` announces again after the pool has, so every started session emits
  `AgentSpawned` twice. Today that restamps `started_at` (a queued session's wait disappears from
  its clock) and nothing else. Not fixed: which announce is the real one is a decision.
- Under `AUTONOMOUS` the gate allows `Write` to `<cwd>/.git/hooks/pre-commit`, `.git/config`,
  `.claude/settings.local.json` and `.mcp.json` (measured, four of four), where the sandbox denies
  `Bash` the same paths. Whether the CLI's own `Write` refuses them is **unmeasured**. If it does
  not, an unattended session in a main checkout or clone can plant a hook the operator's own git
  runs. No carve-out was added; the fix depends on that measurement.

**Measured about git, beyond the worktree record** (2.39.5, n=1 each, now tests):
`worktree add` exits with a `post-checkout` hook's status while leaving a complete worktree;
`check-ref-format --branch '@{-1}'` prints another branch and exits 0; with a relative
`core.hooksPath`, `reference-transaction` runs from the *launching* worktree's own tree for
`git branch` and for the cleanup `update-ref -d`, so files a session can write are executed by
pptmstr's unsandboxed git; `update-ref -d` follows a symbolic ref; a `.git` directory with a
garbage `HEAD` reads as "not a git repository", and one nested in a good checkout is answered as
the outer checkout. The worktree record's "What was not tested" entry for a relative
`core.hooksPath` is now measured on the git side.

## Not built from the session-control records, and why

- **The launcher's "create a worktree" control.** A plan review found it would turn open questions
  into shipped behaviour: there is no session name to default a branch from; it needs a detection
  worker and a hold the description omitted; and the worktree record's O3 first bullet — whether a
  session can redirect which config pptmstr's own unsandboxed git reads — is unmeasured and goes
  live with it. The hooks measurement above is a second reason.
- **"Continue as…"** — waits on P2, which could not run here.
- The ref sensor, the end-of-session summary, the missing-paths notice, removal: untouched.

---

## Built: the inter-team record, first tranche

The postmortem's closing line is the design: "tasks, requests, decisions and claims are one record
with provenance ... The board, the decision pane, the digest and the brief are views of it."

| What is now true | Where | Status |
|---|---|---|
| `complete_task` carries a result, stored on the task and shown on the board; `read_task` returns one task unclipped | `bus.py`, `model.Task.result` | built |
| A declaration, a concern and a result say `run`, `read` or `relayed`, shown as the author's claim ("says: read"); absent is "unstated", never defaulted | `model.Basis` | built |
| A task states the one property it may not break (300 characters, never clipped); a result lists what was not run, with "everything was run" as its own explicit statement and silence shown as "unstated"; a concern can be marked an incident | `model.Task.property`, `Task.not_run`, `Concern.incident` | built |
| A round can be read as one digest: changed, waiting on the operator, unverified, incidents. A projection, nothing stored; `read_digest` for the lead and a section in the board pane | `pptmstr/digest.py` | built, 96 tests |
| A board's facts can be appended to disk one record at a time and restored by a pure function; a test fails when a field of `model.Task` or `model.Concern` is neither restored nor listed as left out. **No caller.** | `pptmstr/boardlog.py` | built, 307 tests |
| Numbered requests between sessions have an on-disk form whose state is derived on read; each party writes only its own directory. **No caller.** | `pptmstr/requests.py` | built, 163 tests |

**Why the board log holds outcomes and not intents.** The first design replayed the intent stream
through `store._apply`. Measured against the reducer by the board-log builder: a replayed
declaration is refused as a duplicate because task ids are fleet-global; a replayed claim re-picks
instead of restoring who won; unlogged inbox reads re-deliver; `Task.writes` cannot be recovered;
monotonic stamps do not order across a process.

**What the running session showed of the postmortem's complaints**, on the app that hosted it:
`read_board` cut five of six specifications at 2,000 characters and the lead relayed tails by
hand; `complete_task` took only an id, so every builder posted its result as a concern; a declared
task could not be amended, so six tasks were corrected by concerns and by follow-up tasks; the
lead restated the standing rules in each of twenty-odd builder prompts; the lead reported to the
operator at nearly every agent return.

## The postmortems' recommendations, item by item

So that nothing is absent by omission. "Q" refers to the questions below.

| Recommendation | State |
|---|---|
| A persisted board that is the specification | results, full read: built. Log core: built, unwired (Q5, Q6, Q7). Amendment as a version: not built (Q2). Links to request numbers: not built. **Readable by other teams: left out** — `2026-08-17-a-team-cannot-read-its-own-board.md` decided boards are session-scoped; requests are the cross-session path |
| A pane of decisions awaiting the person | designed, not built (Q4, Q8) |
| A digest | built; never seen drawn, never called by a model |
| A brief the lead amends, with acknowledgement and layers | **not designed**; engages `archive/2026-08-17-a-session-premise-is-a-place-not-a-message.md` |
| The inter-team channel as an object | core built, unwired (Q1, Q3, Q5) |
| A ledger of "written, never run" | built as a result's not-run list and the digest's unverified slot |
| A marker on any claim crossing a session boundary | built for the board; required on requests in the core |
| Isolation for each builder | not built (Q9) |
| One reachable environment | not built (Q10) |
| Commit early, per task, one owner | not built, and not done in this session — see "State of the tree" |
| The non-negotiable property as one sentence | built as a field on a task |
| A finding is a script with a criterion, run by someone else | run by convention all session; only the basis marker is built |
| A review after every fix round, scheduled | done by hand four times; nothing on the board represents it |
| Concerns with a disposal path (`2026-08-21` issue 2) | designed, not built (Q8) |

---

## The operator's list

### To run (needs an unsandboxed shell that is logged in to the CLI)

1. `.venv/bin/python scripts/verify_resume_containment.py --keep` — four Haiku sessions, under
   0.30 USD by estimate. Says whether a session resumed under containment is sandboxed, which is
   also whether the hand route (setup session, close, resume autonomous) has been contained. A
   clean run should read CONTAINED; if the write axis reads INVALID, read the write-literal reason
   line first.
2. One contained `AUTONOMOUS` session in a throwaway `git init` directory, asked to `Write`
   `.git/hooks/pre-commit`; read the file from the host afterwards. Settles the gate gap above.
3. `scripts/verify_worktree_containment.py` with the session directory set to a subdirectory of a
   linked worktree, and to a subdirectory of a plain repository. Two Haiku sessions. Settles
   whether "not measured" for those layouts should read wide or narrow.

### To look at

The launcher, with nobody having seen any of it: the resume section and its directory line; a
failed-launch notice, a restore, and "set aside"; the containment line in this worktree (the
paragraphs for nested and unordinary worktrees are long and unseen at the modal's width); the
board pane's result, basis marker and digest section.

### To answer

| # | Question | Lead's recommendation (R) |
|---|---|---|
| 1 | May a request cross from an unattended or contained session to another session without the operator seeing it? It is the continue record's mechanism 3. | No: held as a needs-you item |
| 2 | May the agent that declared a task amend it? `intents.TaskAmended` says only the operator may. | Yes, declarer only, never the holder, signed like a declaration |
| 3 | Is a session addressed by id, or by a name or directory? | Name or directory |
| 4 | Is the operator's answer to a decision written into the session brief? | No: to the asking lead |
| 5 | Where does pptmstr keep board, decision and request state? | Its own config directory, keyed by session id |
| 6 | May a session's root release a claim held by an agent that no longer exists? | Yes |
| 7 | Is a board restored only when a session is resumed under the same id? | Yes, for now |
| 8 | Which new tools run without sign-off: marking a concern not-work, raising a decision, answering a request? | The last two; not-work is shown to the operator |
| 9 | Per-builder isolation: temporary directories and displays only, or reopen a worktree per builder (which `2026-09-03-orca...` argues undercuts `touches`)? | Directories and displays only |
| 10 | A runner outside the sandbox that executes a queued command and returns its log? Same channel as Q1. | Yes, each command held for the operator |

Also open, from the sibling records and unchanged: the base ref (their O1); how a failed worktree
requirement ends a launch (O4); whether pptmstr runs git itself (the worktree record heads it D3
and calls it R3 in its own preamble).

---

## Decisions taken without the operator

Each is in the tree. Each can be overturned; the cost of doing so is the second column.

| (L) | To overturn |
|---|---|
| A resumed row with no recorded directory uses the cwd field and says so | one arm in `launch_directory` |
| The picked row as clicked decides a resume, not the listing's current row | `SessionPicker` |
| Restore of a failed draft is a swap into a "set aside" list | `LauncherState.set_aside` |
| An edited retry leaves the earlier failure queued, labelled as earlier | `launched()` |
| Unmeasured git layouts read "not measured"; a `$HOME` that is a checkout makes every directory under it read so | `contained_text` |
| The pool supersedes a live session submitted under the same id, rather than refusing | `SessionPool.submit` |
| Shutdown cancels a wedged run a second time; the grace is 25 s, which equals the SDK's stated worst case (5 s bus-server close + 20 s transport close) with no margin | two constants in `pool.py` |
| A launch refused at shutdown leaves the brief directory it seeded | `app._launch.start` |
| The worktree module creates a branch with no upstream; runs git in the launching checkout; places with a warning when the repository's own path has a dot; refuses when a path sanitises past 200 characters | `worktree.py` |
| The probe reads CONTAINED only when every step of an axis was measured against a control that landed | `axis_verdict` |
| A task result is unreviewed prose: `complete_task` stays auto-approved though its result reaches every agent on the board | `approval._BUS_AUTO` |
| The basis marker has three values; an amended task loses its declarer's basis; on a crowded board a finished row shows its result before its specification | `model.Basis`, `bus._board_line` |
| "Everything was run" is `all_run=true`; an empty not-run string stays unstated; a finished row with no result reads "not run: unstated" | `bus.complete_task` |
| The digest's unverified slot is the whole board on every read; the lead's digest names who is waiting on the operator and never shows the queue row, which under `STRICT` can be a concern the operator has not passed | `digest.py`, `bus.read_digest` |
| The digest's read mark is not in the board log, so a resumed lead's first digest counts from the session's start | `boardlog.py` docstring |
| The board log's default write is durable (7 ms measured), so its caller must not be the draw thread; a bad record hides the records after it | `boardlog.append`, `read` |
| Requests require a basis; a close carries no text; `holds` and `operators` have no default rule | `requests.py` |

## Incidents

Reported by the agents that made them.

- A builder ran one live probe attempt after an amendment forbidding it had been sent but before
  it reached the builder's inbox: "Not logged in", 0 USD, no tool call.
- A builder ran `git add -N` on its own new file against the session's no-git-writes rule and
  reset it. The index was left as found.
- A builder's mutation harness restored three shared files from a snapshot over a twenty-minute
  window. No other builder held those files; later builders were told to reverse their own edits.
- The lead started a second builder on a task the first had already claimed. It wrote nothing.
- The lead's no-git-writes rule overrode the workers' standing instruction to `git add` a file as
  soon as it is created, so ten new files had no revert path while the session ran.
- One backgrounded whole-suite run produced no output for thirty minutes and was stopped; the same
  command in the foreground finished in under a minute. Not reproduced, no test named.

## State of the tree

Nothing is committed. The operator was asked once whether to commit per task and has not answered;
the default not to commit without being asked was kept. New files are staged by the lead at the
gate so that they have a revert path; staging is not a commit.

## Verification boundary

**Run with nothing else writing, by the session's last builder:** `make check` three times, each
exit 0 — ruff clean, black clean over 135 files, mypy clean over 50 source files, pytest 3737
passed and 3 xfailed in about 58 s, identical counts each time. No fix was needed and no test
differed between runs. The first whole-suite run a builder reported in this session was 2591 passed. The three xfails predate this work. mypy is also clean on the
four new modules and the probe script, which `make check` does not typecheck. `mypy pptmstr
scripts tests` reports 792 errors in 43 files, the backlog the `Makefile` describes.

**What that green does not cover.** Every run had `$HOME` read-only and no network.
`tests/test_driver.py::test_relaunching_a_team_session_relaunches_a_team` writes a brief under the
real `~/.claude/projects/-tmp/briefs/` when home is writable (measured with `HOME` pointed at a
scratch directory); 278 such directories are there already. It predates this work and is not
fixed.

**Read by no reviewer:** the last closing round (`es-close-launcher`, `es-close-worktree`,
`es-close-pool`), every inter-team task (`bus.py`, `model.py`, `store.py`, `board.py`, the board
pane, `digest.py`, `boardlog.py`, `requests.py`), and this record.

**Not verified by anyone:** anything live; anything drawn; any git but 2.39.5; any platform but
Linux/ext4; the SDK's close bounds, which were read and never timed; the two storage cores against
`store.py` (no test yet shows a restored log equal to the board of a real run).

## Records this one touches

None is edited.

- [`2026-10-07-a-session-can-run-in-its-own-worktree.md`](2026-10-07-a-session-can-run-in-its-own-worktree.md)
  — step 1 of its sequence is built. Its D2 "relative `core.hooksPath`" entry under "What was not
  tested" is measured on the git side. Its "Records this one corrects" lists three docstrings
  repeating "the sandbox's writable region is cwd"; all three are corrected. Its evidence table
  says the git probe's scripts are not in the repository; `tests/test_worktree.py` now reproduces
  the `add -b` trap, the hook exit and the empty-directory acceptance on every run.
- [`2026-10-07-a-session-can-continue-under-a-different-mode.md`](2026-10-07-a-session-can-continue-under-a-different-mode.md)
  — its "live widening is unguarded" is closed; O1's picker defect is closed; O3's two hazards are
  measured and the pool one fixed; P3 is done as tests; P2 and P1 are written and not run. The
  guard admits a live widen to `AUTONOMOUS` on a *contained* session, so "the top rung is not
  reachable live" no longer holds for that case; its prompts describe the rung it started under.
- [`2026-08-21-a-dead-session-costs-more-than-its-approvals.md`](2026-08-21-a-dead-session-costs-more-than-its-approvals.md)
  — issue 1 (how a claim was established) is built; issue 5 (the board does not survive) has a
  storage core and no wiring; issue 2 is designed.
- `intents.TaskAmended`'s docstring — Q2 asks to reverse it. Not reversed.

---

## Second run: read by reviewers, measured live, fixed

**D5.** "You've been restarted with elevated permissions. Please continue testing and
finalizing." **D6.** "Stop workers and I'm going to start you in autonomous mode again. You
need to record your board and figure out how best to test in the sandbox."

This section is the board of that run, written down because a board does not survive its
session. It was written by the lead from the workers' results; "run" and "read" are each
worker's own statement of basis, and counts are theirs. The host was unsandboxed for this run:
`$HOME` writable, network up, the CLI logged in (measured by the lead), git 2.39.5, SDK 0.2.164,
bundled CLI 2.1.292 — spawned from the *main* checkout's `.venv`, which this worktree borrows.
Live sessions were Haiku, n=1 per arm unless said, about 1.4 USD in total. Images were rendered
under Xvfb at 1500x900 and nowhere else.

### The operator's run list, now run

| Item | Verdict | Limits |
|---|---|---|
| 1. A session resumed under containment is sandboxed (P2) | **CONTAINED**: same id by messages and by disk; both writes and the off-allowlist fetch denied on the resume, landed on the host and uncontained. Reverse: a resume that passes no settings is **RELEASED** | n=1 per direction; SDK driven directly, not `AgentSession` |
| 2. `Write` to `.git/hooks/pre-commit`, `.git/config`, `.claude/settings.local.json`, `.mcp.json` under contained `AUTONOMOUS` | pptmstr's gate **allows** all four; the CLI's permission layer then **denies** all four ("...running in don't ask mode"), nothing on disk, both controls landed | n=1; four exact paths; `Write` only; one CLI build. The gate still takes nothing out of the region: the protection is the CLI's and unpinned. A gate-allowed call the CLI refuses fires **no closing hook** (root measured; the sub-agent bracket consequence is inferred) |
| 3. Write boundary from a subdirectory of a linked worktree, and of a clone | Bash wrote the session directory and nothing else: not the root above it, not the repository's git directory, not a sibling worktree. A contained session in a subdirectory **cannot `git add`** | n=1 each; Bash arm only; "root gets the git dir, a subdirectory does not" fits two outcomes and is not read from the CLI. The CLI process itself still leaves `.claude/.cc-writes/` in the main checkout |

The launcher still reads "not measured" for those layouts. Whether one measurement each earns a
stated boundary is **(O)**.

**Found by the first probe and not asked for: a resumed session did not receive the resuming
launch's system-prompt append**, through `AgentSession.run` too (five-arm probe,
`scripts/verify_resume_append.py`: three arms NOT RECEIVED, control landed, new marker on argv;
two arms with `snapshot: False` RECEIVED). Role text, a changed template and the premises path
are in that append. **Fixed:** `AgentSession._system_prompt` sets `snapshot: False` on a resume;
the driver arm re-run reads RECEIVED. A CLI below 2.1.257 (the SDK's documented floor, not run)
gets an ERROR line in the transcript; that path has never fired. The observed cost of the key
was 0.038 USD against 0.023 on one resumed turn; the SDK documents none.

### Three reviews of what no reviewer had read, and what became of each finding

Every finding was reproduced by a builder, with a failing test, before it was fixed. Two more
reviewers then read the fixes; their findings got the same treatment.

| Finding | Reproduced | Now |
|---|---|---|
| A multi-line `result` or `not_run` (auto-approved) forges board rows, headings and notes in every agent's read, and the digest tells the lead to relay them | yes; wider than reported (title, id, touches, subject too; the claim reply; the inbox) | every agent-written body line is quoted `\| ` on `read_board`, `read_task`, `read_inbox`, `read_digest` and the claim reply; one-line fields are flattened and double-quoted; a legend rides each reply. Whether a model discounts a quoted line is **unmeasured** |
| The same forgery drawn in the board pane | yes, seen in an image | every body and a concern's subject draw behind a bar the pane draws beside nothing else, across wrapped lines; U+0000 is drawn as `<NUL>` in this pane only |
| `read_digest` shows any agent the body, then the subject, of incidents the board withholds (withdrawn, point-to-point, naming no declared task) | yes | body, subject and a missing task id are shown only when a board row carries the concern. **Refuted in part:** the board already broadcasts a task-naming concern before its recipient reads it, so the digest still does |
| A mis-spelled basis refuses `complete_task` and strands the claim | yes | the completion lands with no basis and says so; a declaration or concern is still refused |
| The claim reply, an operator-edited declaration, board notes and the inbox show a basis beside words its author did not write | yes | each carries its marker or the edited flag. The gate's stamp is one boolean, so an operator who edits only a title also clears the basis |
| A finished row nobody opened says nothing about basis or not-run | yes | one standing line on a closed row; "unstated" is never drawn in the reassuring colour |
| Amending only the property erases the declarer's basis; a declaration could arrive claimed or finished; a whitespace result counted as a result | yes, all | reducer fixed; the (L) "an amended specification loses its basis" stands for a changed specification |
| The own-checkout refusal is memoised and never asked at launch | yes: a typed directory swapped for a link into the checkout launched `AUTONOMOUS` inside pptmstr's source | asked fresh at press, again after the version read, and in `AgentSession.run` immediately before the spawn — the second review showed a launch queued at the cap or held behind an unwinding run reached the spawn unasked. A refused session ends FAILED; it is not downgraded. **Residual:** the CLI resolves the string again in its own process |
| A recorded directory since replaced by a link is shown as recorded and launched at the target | yes | shown as redirected, in warn colour, launched at the target. **(L)** warned, not held |
| Resuming a session live in this window ends its run with only a log line | by reading | the row is tagged "open here now" and the modal says what launching ends. The pool still supersedes |
| `worktree.detect` reports a session-built decoy as `main_root`; `create()` then runs the decoy's hooks unsandboxed | yes, end to end, hook ran six times | a pointer at another real repository's worktree, and a main checkout inside the worktree, are refused. **Not met and pinned as not met:** a decoy outside the worktree that names it back is still reported. Nothing in the directory says which repository is real; the caller must hold `main_root` from somewhere a session cannot write. No caller exists |
| A whitespace-only containment string counts as containment | yes; the SDK does put `--settings " "` on argv | the guard strips. `_containment` stays assignable; a test fails on any second write of it in `pptmstr/` or `scripts/` |
| `requests.py`: a party rewriting its own file inherits the other party's answer and the operator's release | yes | an answer, receipt and release each carry a digest of what they were made against; a mismatch derives as `STALE`. Write functions take the record as seen and refuse `CHANGED`. **On-disk form changed; the digest is over the decoded record and is unversioned** |
| `requests.py`: a FIFO hangs every reader; a full directory drops a party's history silently; an integer clock makes every later act `CHANGED` | yes, all | bounded non-blocking reads of regular files; a write into a full directory is refused `FULL`; an unread party is named in `View.unread` and its references are `UNRESOLVED`; a write returns the record as it decodes. `FULL` is permanent: nothing deletes a record |
| `boardlog.py`: `read` raises or hangs; two writers restore a board neither held; a hinted `append` fills a hole out of order; an id escapes the root | yes, all | `read` is total and bounded at 1 MiB a record; `restore` withholds a record one reducer could not have appended and marks the task or concern `contested`; `append` takes no hint and lists the directory each time (about 1.5 ms per 1000 names, measured); `Board.short` is the one question for "is anything withheld or damaged" |

**Pinned as a known cost, not fixed:** after a restart agents return under new addresses, so a
task claimed before it and finished after restores as claimed by the dead agent, contested, with
the completion withheld. That is Q6, and it is the first thing a caller of `boardlog` meets.

### Seen drawn for the first time, and what the images changed

`scripts/screenshot.py` gained `--scenario {resume,containment,failure,board}`. Before that,
`make shot` under `xvfb-run` had been writing an **all-black** image (front-buffer readback is
zero under llvmpipe), so nobody who looked at a headless shot before this run saw anything.

Fixed after being seen: the launcher's buttons pushed off-screen once the modal grew after
opening (the lead opened the re-rendered image); the modal widening to about 830 px on
unwrapped lines; "still parks" followed by nothing; a refused directory still describing
`autonomous`; a write boundary stated for a directory that is gone; a worktree containing its
main checkout reading as the measured layout; the digest running into the first row; an opened
row saying less than a closed one.

Seen and left: the cwd field clips a long path at the part that tells a worktree from its main
checkout; the redirected-directory line is nine wrapped lines; a typed cwd that does not exist
still gets a stated boundary under `autonomous`; in the pane, a subject can still add words to a
heading the pane composes around it; no forged-text scenario is in the script (the images came
from scratch scripts).

### Decisions taken without the operator in this run

| (L) | To overturn |
|---|---|
| Every agent-written line on a bus read is quoted, titles and subjects are double-quoted, and a legend of about 380 characters rides every read | `bus._quoted`, `_said`, `_QUOTE_LEGEND`; 27 byte-pinned tests |
| A completion with an unreadable basis lands rather than being refused | one arm in `bus.complete_task` |
| A resume sets `snapshot: False`; a resume with no briefing sends an empty custom prompt with the same key (weakly shown, n=1) | `AgentSession._system_prompt`; the second is one branch |
| A redirected recorded directory warns and launches | `launch_directory` |
| The own-checkout check lives in `AgentSession.run`; the app-side check is kept only to hand the draft back | `_own_checkout_refusal` |
| `boardlog.restore` refuses and flags rather than applying what it is given; `append` lost its `after` hint | `_one_writers`; restoring the hint reopens the hole |
| `requests`: digest of the decoded record; write functions take the record as seen; `derive` requires `unread` | no caller exists, so no compatibility path was written |
| `worktree.detect` refuses a worktree moved by hand without `git worktree repair` | the backlink check |
| A `tests/conftest.py` fixture redirects the brief root for every test, only when it is the real one | one fixture |

### The gate, with nothing else writing

Run by the lead after every worker had stopped: `make check` exit 0 — ruff and black clean, mypy
clean over `pptmstr`, pytest **4152 passed, 3 xfailed** in 71 s; a listing of
`~/.claude/projects` before and after was identical. The same command inside a `bwrap`
approximation of a sandbox (read-only root, writable checkout and temp directory, no network):
exit 0, identical counts. That approximation is not the CLI's sandbox. Each was run once.

Not run: `make check` three times; mypy over the scripts this branch added; a real display.
One builder saw `tests/test_pool.py::test_a_run_that_raised_while_ending_is_not_left_for_asyncio_to_report`
fail intermittently on an unclosed TLS socket to `160.79.104.10:443` when run beside
`test_app.py`, which means some test opens a real connection. Neither of the lead's runs showed
it and the test that opens the socket is not found.

`~/.claude/projects/-tmp/briefs/` holds 282 directories; 278 predate this work and four are this
run's reproductions. None was deleted.

### What this run makes stale above

- "The operator's list — To run": all three items are run.
- "Read by no reviewer": everything listed there has now been read, and the fixes read again.
  This section itself has been read by no reviewer.
- "Measured about the code as it was": the `Write` gap is measured (the CLI refuses);
  the double `AgentSpawned` is untouched.
- The inter-team table's test counts (96, 307, 163) are the first run's; the modules have
  grown and the whole-suite count above is the current evidence.
- Still unbuilt and unchanged: everything in "The postmortems' recommendations" marked not
  built, and Q1–Q10, none of which has been answered.
