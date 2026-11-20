# Decisions owed to the operator after Round 4

Round 4 is committed through 8837b76, and `make check` is green: 16,730 passed. Six decisions
remain. The designs left each of them open, and none can be settled by building. Each one below
gives the question, what the tree says today, the case for each answer, and the lead's
recommendation.

The sources are the designs in `planning/2026-11-07-*.md` and the module plan's "Round 4
outcomes". Facts marked *(run)* were measured at 8837b76 for this file. Everything else is
reading.

| # | Decision | Reversible? | Blocks anything? | Lead recommends |
|---|---|---|---|---|
| 1 | Build B9 stage 2 (one-line arms)? | yes, a pure move | no | **defer** until H+ → P is scheduled, then build it first |
| 2 | Close `_peer_waking`/`_moved`'s open `case _`? | yes | no | **pin, don't close** |
| 3 | fsync the UI-thread saves? | yes, one argument per call site | no | **yes for `settings`, no for `sessions` and `layout_ini`** (weakly held) |
| 4 | Should `replace` keep the file's mode? | yes | no | **yes, keep it** |
| 5 | Drop `create_once`'s `durable` flag? | yes | no | **no, keep it** |
| 6 | Cut the dependency lock? | yes, regenerable | a fresh bootstrap can't run tests | **yes, but pin loop scope first** |

---

## 1. Build B9 stage 2: every `_apply` arm a one-line call?

**The question.** Stage 1 moved the store's helpers into per-entity modules. The `match` in
`store/__init__.py` still holds every arm's body. Stage 2 ("M+") turns each arm into
`case X(): step = tasks.claimed(snap, intent, now)`, with the body moved next to its helpers.
The arms return a `Draft` (the twelve slices an arm may write) or `Unchanged`, and `Draft` and
`Step` live in a leaf `store/step.py`.

**Today** *(run)*: `store/__init__.py` is 1,779 lines, with 55 `case` lines over 57 intents.
The ten entity modules hold 3,221 lines.

**For building it**
- **The match becomes the table it is.** The design estimates about 115 lines, against today's
  1,262-line match. "One place to read" is the reason `_apply` exists (B9 §2, property 3), and
  at 1,262 lines it is only nominally true.
- **Static exhaustiveness stays.** mypy still names a missing variant at `assert_never`, and
  each handler is typed for its variant (probe 3). That is what keeps H+ → P's new intents
  *forced*, the premise of the recorded P count (B9 §3).
- **An entity's code ends up in one module.** A reader of `store/tasks.py` would see the task
  arms beside the helpers they call. Today they are split between two files.
- **It is mechanical.** `nodes[...] = ...` becomes a local built from `snap` and returned in
  the `Draft`. Pin P3 (two exits, 29 arms) already exists and catches the most likely
  translation error, an early return that falls through.

**Against it, or against building it now**
- **One hop to read an arm.** Debugging a transition means opening a second file. Today the
  body sits under its `case`.
- **A new type with a contract.** `Draft` must name exactly the slices an arm may write, and the
  tail owns the rest. The contract is a gain if enforced and a trap if it drifts. A thirteenth
  slice (P's `Snapshot.peers`) touches `Draft`, `Draft.of` and the tail.
- **mypy narrowing through one-line arms is unproven at scale.** Probe 3 ran on a two-variant
  toy under `--strict --python-version 3.11`, not on the store (B9 §5). The first stage 2 commit
  would show it.
- **It collides textually with H+ → P.** Stage 2's `mail`, `peers` and `restore` moves edit the
  same bodies P rewrites. The two must not run at the same time (B9 §3). P is not scheduled, so
  building stage 2 now settles the order: P then rebases onto moved code.
- **Cost.** This is the hottest file (30 commits in 90 days at the design's count), and the
  patch is about 1,200 moved lines. B9 stage 1 needed two disk-full reruns and a 25-file
  citation grant.

**Recommendation: defer, and decide it together with H+ → P.** Stage 1 is a coherent stopping
point, as the design says. If P is scheduled, build stage 2 first: P's new arms then land as
one-line cases, and its `mail` split happens in `store/mail.py` rather than inside the match. If
P is not coming, stage 2 is a readability win with a real cost, and nothing waits on it.

---

## 2. `_peer_waking` and `_moved`: close the open `case _`, or pin it?

**The question.** The tail after every arm calls two functions that re-dispatch on the intent:
`store/waking._peer_waking` *(run: 7 arms, then `case _: pass`)* and `store/waking._moved` (4
arms, then `case _: return ()`). D-c closed `channels.outcomes` with `assert_never`. Should these
two follow?

**For closing them (`assert_never`, listing every intent)**
- **Consistency with D-c and D-d.** D-d says "the open matches are closed or pinned", and D-c's
  hazard is real: an arm that should change persisted state can have no case and nothing flags
  it.
- **A new intent forces a decision.** When H+ → P adds `PeerMessage` intents, mypy would point at
  both functions as well as `_apply`. With the open default, a new peer intent that should wake
  a lead silently doesn't.
- **The waking rules are the subtle part.** The `_moved` docstring spends a paragraph on why an
  agent's act must not reset a chain (two agents keeping each other busy). A forced review of
  every new intent against that rule is worth something.

**For keeping them open (pinned as deliberate)**
- **The default is the design.** Both docstrings argue that most intents neither wake nor move a
  link. `_moved`: "Nothing an agent does moves it." Closing them means a `case A() | B() | … :
  pass` naming about 50 intents, and that list carries no information.
- **The noise hides the signal.** A reader looking for what wakes would scan 50 names to find the
  7 that matter. The open form reads as "these, and nothing else", which is the rule.
- **D-c's hazard is narrower here.** `outcomes` maps intents to persisted facts, and a miss
  loses data on disk. A miss here is a missed wake or an un-reset chain. Those are in-memory and
  visible, and the chain bound and the operator's release button backstop them.
- **D-d allows "pinned".** A test can hold the open default to a stated set, for example "the
  intents `_peer_waking` acts on are exactly these 7". Then adding a case is a deliberate edit,
  and a new intent shows up in the test's diff of `get_args(Intent)`.

**Recommendation: pin, don't close.** Add a test that lists, for each function, the intents it
handles and the intents it deliberately ignores, and fails when `get_args(Intent)` gains a member
neither list names. That gives the forcing property (a new intent must be classified) without
50-name `pass` arms in the source. Record it as a D-c clarification beside B4's: D-c governs
Intent→facts maps, and Intent→wakes maps are pinned by classification.

---

## 3. Should the UI-thread saves (`settings`, `sessions`, `layout_ini`) fsync?

**The question.** `ondisk.replace(target, data, *, durable)` takes `durable` with no default, so
every caller states its choice. `sessionfile` passes True. `settings`, `sessions` and
`layout_ini` pass False, which reproduces what they did before B2. B2 found that nothing argues
that choice: "their silence is not a decision".

**What `durable=False` risks.** A process crash leaves the old file whole: the rename is atomic.
A power loss or kernel panic can leave the *new name over an empty or short file* if the
filesystem commits the rename before the data. ext4's `auto_da_alloc` usually masks this for
rename-over. B2 relayed that and did not measure it, and it does not hold on every filesystem
or on macOS.

**For fsync (durable=True)**
- **`settings.json` is the operator's configuration.** Losing it to an empty file after a crash
  resets every preference, the policy defaults included. That is the file a user would least
  expect a crash to eat.
- **The cost is small for rare writes.** `settings` saves on a deliberate change (a theme, a
  dialog's OK), not per frame. One flush per click is not perceptible on an SSD.
- **It makes `layout_ini`'s docstring true.** It says a crash "leaves the old file whole". With
  fsync that holds for power loss as well, not only for a process crash.

**Against (keep False)**
- **They run on the UI thread.** A flush on a slow or busy disk (spinning disk, network home,
  heavy I/O) stalls a frame. `sessions` saves on a bookmark click. `layout_ini` may save on
  every dock move, so how often it fires sets the cost, and that wasn't measured.
- **The worst case is recoverable.** An empty `imgui.ini` means a default layout. An empty
  `sessions` file loses bookmarks, not sessions: the session files themselves are durable.
- **A durable write is still not a correct read.** If an empty `settings.json` is the danger,
  the robust fix is a reader that treats an empty or unparsable file as "use defaults and say
  so", which `settings` may already do. That covers every cause, not only power loss.

**Recommendation (weakly held): `settings` → True. `sessions` and `layout_ini` stay False,
with the reason written at the call site.** `settings` is rare, deliberate and the costliest to
lose. The other two are frequent, or cheap to lose. Before deciding `layout_ini`, measure how
often it saves. If it is per-dock-move, False is clearly right.

---

## 4. Should `ondisk.replace` keep the target file's mode?

**The question.** `replace` writes through `mkstemp`, which creates 0600, and the rename
carries that mode. B2 measured it *(q3)*: `settings.save` turned a 0644 `settings.json` into
0600. Every rename-based save does the same.

**For keeping the original mode (stat the target, chmod the temp before the rename)**
- **Least surprise.** A user who sets 0644 on `settings.json` to share it, or whose umask gives
  0644, finds it 0600 after the first save. That is a silent change to a file they own.
- **Tools that read the file as another user break.** A backup agent or a dotfile manager
  running under another uid loses read access after one save.
- **It is cheap.** One `stat` and one `fchmod` on the temp fd, inside the primitive, for every
  caller at once.

**For leaving 0600**
- **0600 is a safe default** for files that may hold paths, session ids and policy. No pptmstr
  file needs group or world read.
- **Matching an existing mode carries a looser mode forward.** If a file was once
  world-writable by mistake, `replace` would keep it so.
- **Nothing has complained.** B2 found nothing arguing either way.

**Recommendation: keep the target's mode when it exists, and 0600 when it doesn't.** The
security argument for 0600 is about new files. For an existing file, the operator's chmod is
the more specific instruction, and silently overriding it is the kind of surprise CLAUDE.md
warns about. Add a pin: a 0644 target stays 0644, a new target is 0600.

---

## 5. Should `ondisk.create_once` drop its `durable` flag?

**The question.** `create_once(directory, names, data, *, durable)` has one production value,
True. Only tests pass False (eight sites, through `boardlog.append`'s public `durable`), to skip
fsync for speed.

**For dropping it (always fsync)**
- **A flag with one production value is a test seam in the API.** STYLE's smells include
  parameters that exist for tests. A production caller could pass False by mistake and lose
  the durability `boardlog`'s docstring argues for.
- **Fewer states to reason about.** Every created record is durable, full stop.

**For keeping it**
- **Test speed.** fsync per record across thousands of board-log tests costs real time.
  `boardlog`'s docstring puts it at milliseconds per record, and the suite already takes about
  8.5 minutes. This was not measured for this file.
- **It is explicit, not a default.** The flag has no default, so every production caller
  writes `durable=True`, and a False shows up in review.
- **Dropping it costs eight test edits** plus a decision on how tests avoid fsync: a monkeypatch
  of `os.fsync`, which B2's patch-site rule allows, since the primitives call `os.fsync`
  through the module attribute.

**Recommendation: keep it.** The keyword-only, no-default form already makes production intent
visible, and replacing it with a monkeypatch moves the seam rather than removing it. Revisit if
a production caller ever passes False.

---

## 6. Cut the dependency lock with `make lock`?

**The question.** `make check` runs `lock-gate`, which passes vacuously because
`requirements-dev.lock` doesn't exist. Without a lock, `./bootstrap.sh` resolves fresh. On
2026-11-06 that gave anthropic 2.6, claude-agent-sdk 0.2.178 and pytest-asyncio 1.4.1.post1, and
pytest then **failed at configure**: pytest-asyncio warns that `asyncio_default_fixture_loop_scope`
is unset, and `filterwarnings = ["error"]` makes that an INTERNALERROR. This worktree's `.venv`
was pinned to main's freeze by hand to keep a comparable baseline.

**For cutting it**
- **A fresh checkout can't run its tests today.** That is a broken bootstrap, not a preference.
- **The 2026-10-11 Mac-install plan already owes it.** `make lock` writes both files with
  hashes, and bootstrap installs with `--require-hashes`.
- **lock-gate starts checking something.** Today it is a green step that verifies nothing.

**Against, or what to watch**
- **The first lock resolves newest.** With no lock to keep versions from, `make lock` takes
  today's latest, which is the set that broke pytest. Cutting it without the pyproject fix
  locks in a red suite.
- **It needs the network and uv**, so it is an operator-run command outside this session's
  sandbox.
- **It changes the baseline.** anthropic 1.14 → 2.x is a major version. The suite's counts
  after the lock are a new baseline, not comparable with Round 4's 16,730.
- **It is a dependency-pin change**, which the module plan already marked as the operator's
  call.

**Recommendation: yes, in this order.**
1. Set `asyncio_default_fixture_loop_scope = "function"` in `pyproject.toml`'s pytest options,
   so a fresh resolve configures.
2. Run `make lock` on a networked machine. Then `./bootstrap.sh` into a clean venv.
3. Run `make check` and compare the result against 16,730, treating any difference as the
   upgrade's.

Do it on its own branch from main, not on `chore/refactor-work`, so the refactor's baseline and
the upgrade's diff stay separable.

---

## Decided (operator, 2026-11-09)

1. **B9 stage 2: deferred**, to be decided with H+ → P.
2. **`_peer_waking`/`_moved`: pinned, not closed.** A test classifies every intent as handled
   or deliberately ignored, and fails on an unclassified one. The D-c clarification goes in
   the module plan.
3. **UI-thread fsync: the operator asked "why not yes for all?". The lead now recommends yes
   for all three.** A trace at 8837b76 (run, by reading call sites) found that none of the
   saves is frequent:
   - `layout_ini` writes once per launch, before the runner starts (`app.py`,
     `layout_ini.keep_layout_for`). hello_imgui's own saves of `imgui.ini` do not go through
     pptmstr. So it never touches the UI thread mid-run. The section above guessed "every dock
     move", and that guess was wrong.
   - `sessions.save_overlay` runs on a bookmark click (`sessions.set_title` has no caller), and the launcher's own
     docstring argues for durability: "a bookmark that survives only a clean shutdown is a
     bookmark lost by the crash it was made to protect against".
   - `settings.save` has one caller (`app._save`), on a deliberate change.

   The case against rested on a frame stall from frequent saves, and there are none.

   **Probe (run, 2026-11-09, this machine).** The machine is ext4 on LVM, the filesystem is 98%
   full, and `$TMPDIR` is on `/`. `ondisk.replace` was timed with durable True and False
   alternating, at 1, 8 and 32 KB (`settings.json` is 658 B), and the probe script was deleted
   afterwards.

   | Disk state | durable=False median / p95 / max | durable=True median / p95 / max |
   |---|---|---|
   | idle (n=200 per cell) | 0.3 / 0.4 / 0.9 ms | 7.0 / 8.2 / 15.3 ms |
   | bulk write pending writeback (2 GB, then 3 GB; n=60 and 40, 1 KB rows) | 0.3 / 13–127 / 95–179 ms | 7.1–7.3 / **214–989 ms** / **1.96–3.63 s** |
   | sustained write whose files were deleted before writeback (n=40) | 0.3 / 0.4 / 0.5 ms | 7.0 / 10.0 / 14.6 ms |

   - **Idle.** An fsync costs about 7 ms, roughly half a 60 fps frame. That is invisible on a
     click.
   - **During writeback of other data.** An fsync of a 1 KB file waits on the journal, behind
     everything else being flushed: about a second at p95 and up to 3.6 s here. Even a
     non-durable replace reached 179 ms. This machine is in exactly that state whenever a
     builder runs the suite or git writes a large tree.
   - **Measurement gap.** The 8 and 32 KB rows ran after each background write had finished, so
     only the 1 KB rows measured the loaded case. Nothing was measured on macOS or an SSD
     without LVM.

   **What follows.**
   - `layout_ini` → **durable**. It runs once, before the runner starts, so no frame waits on
     it, and a multi-second worst case only delays startup.
   - `settings` and `sessions` → **durable, but not on the UI thread.** Durable on the UI thread
     means a click can freeze the app for seconds on a busy disk. Non-durable means a power
     loss can lose the operator's configuration or the bookmark the launcher's docstring says
     must survive a crash. Moving the write off the frame removes the trade-off. B3's
     `SerialWorker` already serialises the board and channel writers, and the same worker can
     take these two saves. That is a design change (the save reports its refusal later, not
     inline), so it gets its own small design before building.
   - **Interim**, until that exists: `durable=True` for both. A rare multi-second freeze on a
     deliberate click is visible and recoverable. A lost `settings.json` is silent.

   Confirmed by the operator ("So, continue?", read by the lead as confirming the plan, and
   said so when the round started). Built in Round 5: all three saves are durable, and the
   off-thread design is `planning/2026-11-09-ui-saves-leave-the-frame.md`.
4. **`replace` keeps an existing file's mode; a new file is 0600.** This section's premise
   was stale. B2's build (3cb1528, 2026-11-07) had already made `replace` keep the target's
   bits. The "Rename-based saves leave files at mode 0600" line in the module plan's
   defect list described the code before B2. Round 5 closed the one remaining gap: an
   `lstat` that failed for a reason other than absence used to fall back to 0600 over the
   existing file. It now raises before writing (14d24cd).
5. **`create_once` keeps `durable`.**
6. **pyproject first:** `asyncio_default_fixture_loop_scope = "function"` is set (it is
   pytest-asyncio's default, so nothing changes in behaviour). On the current `.venv`
   (pytest-asyncio 1.4.0), collection gives 16,768 tests and the async bridge and driver tests
   pass. A fresh resolve was not tried here: it needs the network. `make lock` stays for the
   operator, on its own branch.

## Decided after Round 5 (operator, 2026-11-09)

- **The bookmark-during-scan defect** (planning/2026-11-09-ui-saves-leave-the-frame.md):
  deferred. It is built with that design, first in its move order, not on its own.
- **The off-thread design's three questions:**
  - A save that fails to write is **shown**, not swallowed. STYLE.md §1's `settings.save`
    example changes with the build.
  - The module is named **`prefsync`**.
  - `SerialWorker`'s reason text becomes **neutral**, not "append".
- **`replace` keeps masking off setuid, setgid and sticky** (`& 0o777`).
- **Before merging:** `make verify` was run in the session. `make smoke` and `make shot` were
  asked of the operator (they need a display), and so was one interactive `make run`.

## Not a decision now

- **B6 step 5** (Snapshot methods → `channels/projections` functions, about 250 call sites). Its
  stated benefit is H+ → P's, so it waits until P is scheduled. When P is, decide it together
  with decision 1 above.
- **Before merging this branch:** `make verify`, the GUI targets, and one launch of the app. None
  ran in Round 4.
