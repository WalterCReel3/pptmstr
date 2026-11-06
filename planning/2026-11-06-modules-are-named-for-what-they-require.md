# Modules are named for what they require, not for the noun they share

**Dated:** 2026-11-06 · **Status:** research complete and scrubbed by execution. Nothing is
built yet; the three build rounds are set out under "Build rounds", and the operator approved them.
Decisions D-a, D-b and D-c are decided. · **Asked by:** the operator: "a lot of modules with
shared prefixes ... a hint of a need to refactor", read through Stepanov's algorithmic taxonomy and
Norvig's compression. · **Evidence:** a seven-task read-only investigation (doctrine, four cluster
taxonomies, the monoliths, a static graph) and a skeptic pass over all of it. A second round then
*ran* the claims that carried weight:
- eleven probes, in `planning/2026-11-06-refactor-graph/probes/` (`results.txt` holds the
  outputs);
- the settling graph runs and per-file churn (`settle.py`, `settle.json`, `churn.csv`);
- a planning scrub (`planning-status.csv`).

A line marked *run* was executed; everything else is **read**. File:line references were taken at
b72a48a; each also names its symbol.

**Baseline** (run, b72a48a): `make check` passes with 16142 passed, 15 skipped and 23 xfailed;
ruff, black and mypy are clean. The environment is the worktree `.venv`, pinned to main's exact
package set. See "Environment" below for why that pinning was needed.

## The answer in one paragraph

The premise does not hold as a mandate to consolidate. The prefixed families
(`board*`, `command*`, `sessionfile`/`fleetsync`) are mostly **one domain split one module per
layer** (pure value → format/fold → disk → thread → bus/text). For `command*` that split is
recorded, and it is how this team builds: one file per build step, so parallel workers never touch
the same file (`planning/archive/2026-10-13-building-the-break-glass-command.md:13-27`). Merging
prefixed files would raise board contention and buy nothing. The real defects run *across* the
prefixes:
- one module named for its first user that is actually a shared substrate (`boardlog`);
- the same small algorithms hand-copied four or five times, with the copies drifting;
- concepts with no home at all: inter-lead channels (~2,500 lines over five files), wakes, the
  address book and id minting.

The Stepanov move is to name and pin those concepts by the requirements they impose. The Norvig
move is to notice that the functional core is already five functions, and to make the layout show
that.

## The two lenses, made checkable

These are the primary-source principles. The Elements of Programming (EoP) quotes were read from
the authors' PDF. The interview, PAIP and lis.py quotes came through a summarising fetch and must
be checked before anyone prints them as quotations.

- **A concept is a set of requirements on types; an algorithm is filed under the weakest one it
  needs.** EoP §1.7 p. 10: "a collection of requirements a concept. Types represent species;
  concepts represent genera." §6.2 p. 89 gives the weakest-iterator example.
- **Concepts are discovered from algorithms, not decreed.** EoP Afterword p. 226: "For every
  algorithm, we need to discover its conceptual requirements"; "Limiting ourselves to a single
  variety of interface would be a false economy."
- **Regularity.** EoP §1.6: a procedure that reads a clock is not regular. This is already
  STYLE.md:41-53 (`now` is a parameter).
- **Norvig, data-driven programming and compression** (PAIP ch. 2; lis.py, relayed). Choose the
  representation first, then write a few functions over it. Where behaviour is a table, the table
  is the source.
- **Python analogue** (PEP 544, relayed). `typing.Protocol` gives structural conformance, and a
  module object can satisfy one. A Protocol plus one contract test is the closest thing Python has
  to a C++20 concept. `channels.py` already does exactly this with `ChannelLog` and a contract
  test in `tests/test_channels.py`.

Scoring tests for a layout, each checked against existing doctrine:

| # | Test | Agrees with | Tension |
|---|---|---|---|
| T1 | Each model of a recurring concept satisfies one written Protocol and passes one contract test. The Protocol comes after the models exist. | STYLE §1, channels precedent | None |
| T2 | An algorithm appears once, under its weakest requirement, or its copies are pinned by a test. | STYLE.md:181-184 ("unpinned duplication is not") | Each copy argues its own variant (STYLE.md:234-238). A shared version takes the variation as a parameter or does not exist. |
| T3 | A module's name says what it requires (pure / disk / thread / imgui), and an import-layer test enforces it. | STYLE §1, the store-imports-no-disk-shell rule (2026-09-01:55-57) | No such test exists: the rules are prose, and a grep of `tests/` found none. |
| T4 | A closed set of variants is a union checked with `match` + `assert_never`. An open set of models is a Protocol. Never a base class. | STYLE.md:18-32 | Do not replace Intent/Effect with a Protocol. The closed match is the safety argument. |
| T5 | Where behaviour is a table, the table is the source. | `t-bus-auto-reasons` (2026-09-21:104-108) | Stops where the data would be its own oracle: `t-pin-corpus-drift` (:109-111) keeps the test corpus hand-written on purpose. |
| T6 | A new reader finds a domain's few functions from its package `__init__` alone. | STYLE "derive; do not store" | None |
| T7 | A pure module reads no clock and does no IO. Check by grep. | STYLE.md:41-53, q1-fit P11 | None |
| T8 | A move does not orphan file:line citations. | orchestrator-design invariants I1-I8 are cited by number | **No recorded rule.** Needs a decision; see below. |

## What the graph says (run), and what it does not

`planning/2026-11-06-refactor-graph/graph.py` is stdlib-only and runs in about 11 s from the repo
root. `tables.md` and the CSV/JSON files beside it are its output. The narrative its author wrote
is on the board task `graph`. Its `findings.md` was not written because the harness refused it.
This section stands in for it.

- 97 modules and 103,758 lines. Eager imports have **no cycles**. Function-local imports close
  three:
  - {approval, board, model, templates};
  - {bus, commandbus};
  - a 12-module knot of `figures.*`, `theme` and `ui.typeset.*`. This knot holds the only cases of
    non-ui code importing ui, and it confirms the cycle cluster-ui found by reading.
- **The board\* split stands without any community detection.** `module_edges.csv` weights:

  | Edges | Weights |
  |---|---|
  | Inside the persistence half | boardfacts→boardlog 32, boardsync→boardlog 43 |
  | Inside the read half | digest→board 14, boardgraph→board 4 |
  | Between the halves | 2 in total (`address_in_session`, `ROOT_NAMES`) |

  Both cross edges are addressing names, so moving addressing out leaves the halves sharing
  nothing.
- channels→boardlog weighs 39, more than anything between the board halves. **boardlog is
  cross-domain substrate wearing the board prefix.**
- **`command*` proper is one community** together with `ui.command_view`. Its supposed satellite
  `approval` never lands with it (0 of 30 runs). It sits with `templates`, `ui.launcher`,
  `ui.gate_menu` and `sandbox`, which together are launch-time policy.
- **`session*` is homonyms.** `sessions` (the SDK listing used for resume) sits with the launcher.
  `sessionfile`, `fleetsync` and `channels` sit with the journals. "link" names four unrelated
  things: hyperlinks, inter-lead links, link colours and git linked worktrees.
- **The ui suffixes carry no shared contract.** Cohesion is `*_view` 0.005, `*_form` 0.0,
  `*_pane` 0.058 and `*_menu` 0.10. Each widget attaches to its own domain. The pane concept exists
  three times over: `PANE_MODULES` in `tests/test_command_view.py`, title strings in `app.py`, and
  the `Pane` enum in `ui/typeset/roles.py`.
- **Monolith seams** (Louvain Q over top-level defs, with match arms expanded):

  | Module | Q | Split |
  |---|---|---|
  | `store` | 0.815, cut 12/157 | Cleanest in the tree. `_apply`'s arms group by entity: restore 693 lines, peer-mail waking 628, task lifecycle 219, declaration 196, commands 113, claim 104. |
  | `driver` | 0.718 | `AgentSession` splits as: tool gate/park 705, run/identity 531, SDK options/hooks 510, policy/containment 419, sub-agent settling 316. |
  | `model` | 30 components | A catalogue of entities. |
  | `bus` | 0.515 | About 3,000 of its lines are pure text renderers behind 20 `@tool` handlers. |
  | `app` | 0.617 | One mass with sub-themes. |
- **Not reachable from `__main__`:** `requests` (1,495 lines; recorded as deliberately unwired)
  and the three `ui.splash*` modules.

**Do not cite the headline "prefix modularity 0.079 vs community 0.40" as "the prefixes are about
random".** The skeptic showed by reading that the comparison is biased by how it was built.
`settle.py` then ran the checks (run):
- **What the prefixes actually cost.** Unconstrained Louvain scores 0.423. Holding the 7 prefix
  clusters together scores 0.383; holding only the 3 domain prefixes scores 0.396. No single
  cluster costs more than 0.012. The prefixes are therefore cheap as *groupings*. They are not
  themselves communities: Louvain just nests each one inside a larger community.
- **Null models, 100 each.**
  - Unweighted, degree-preserving: real 0.354 against 0.248 ± 0.009.
  - Equal-weight swaps: real 0.423 against 0.381 ± 0.009.
  - Weights kept on their edge slots while edges move: the null scores 0.540 ± 0.021, *higher*
    than the real graph.

  There is real structure, but the margin is modest. Do not quote 0.42 as "strongly modular".
- **Reweighting.** The board\* split appears in only 57% of shuffled runs on raw weights, 0% on
  unit weights and 23% on log(1+w). It rests on the raw edge counts above, not on Louvain. The
  "persistence community" C3 does not survive reweighting (Jaccard 0.22 / 0.46). It was an
  artefact of exhaustive Intent matches being counted as weight (boardfacts→intents 58,
  fleetsync→intents 63, channels 3). One group does survive unit weights:
  {boardlog, boardsync, channels, fleetsync, sessionfile}, the users of the journal substrate.
  That is weak support for B1.

**Do not design a package from community membership.** Use the raw edges. The static graph also
cannot see:
- intent routing (driver→store);
- the 20 MCP tools called by name;
- instance and method coupling;
- 36 `getattr` calls with computed names;
- `KIND`-string record decoding;
- SDK hook callbacks;
- tests that read source by path.

## The functional core today: five functions (Norvig)

Everything else either queries a `Snapshot` or renders it to text.

1. **step:** `store._apply(snapshot, intent, now) → (snapshot, effects)`
2. **derive:** a fixed chain of five snapshot→snapshot steps (store.py:1798-1802)
3. **outcomes:** `(before, intent, after, now) → facts`, one per persisted slice. Already typed as
   `store.Observer`.
4. **restore:** log → restore intent
5. **translate:** `Translator.handle`, SDK message → intents

The layout should make these five discoverable. They are spread across `store`, `boardfacts`,
`channels`, `fleetsync`, `app._record_commands`, `boardlog`, `commandfile` and `driver`, and no
name groups them. Note the limit of this compression. The four `outcomes` implementations differ
on the property that matters:
- `boardfacts` and `fleetsync` are closed matches.
- `channels.outcomes` has `case _` (channels.py:725) and no `now`.
- `app._record_commands` diffs snapshots instead of reading the intent, and mutates `AppState`
  while it observes.

A Protocol over all four would type-check all four and enforce none of the safety argument
`boardfacts.py:13-19` makes. **The concept is "an exhaustive Intent→facts map plus a writer", not
"the journal triple".** The triple is already recorded in
`planning/2026-10-17-sqlite-inspection/q1-fit.md` §5 (axioms P1-P14).

## Proposals, ranked by verdict

### Survived review: small, independent, low risk

Each item is one builder task with a pinning test written first.

| # | Change | Basis | Why it is the Stepanov/Norvig move |
|---|---|---|---|
| A1 | **`naming.py`** takes the addressing helpers out of `board.py` (`address_for`, `reserved`, `role_name`, `ROOT_NAMES`, `address_in_session`). | read; edges run | They need only string maps, not a Snapshot. This removes both board-half cross edges. `driver`, `boardlog` and `boardfacts` import only these names from `board`. |
| A2 | **`read_regular(path, limit)`** opens with `O_RDONLY\|O_NOFOLLOW\|O_NONBLOCK\|O_CLOEXEC`. It replaces the four bounded reads: `boardlog._file_bytes`, `commandfile._file_bytes`, `sessionfile._file_bytes` and `requests._contents`. Channel records are read through `boardlog.read`, so this also covers channels (run, p05). `channels._cannot_be_read` is *not* a read: it classifies damage. With `O_NOFOLLOW` added naively, ELOOP would make it return "transient" for a planted symlink. Leave it out of A2 and decide it separately with its own test. | run (p05) | One concept with one requirement. It also fixes the drift (see Defects). |
| A3 | The three identical `clip()` bodies (`detail.py`, `brief_pane.py`, `board_pane.py`) become one. | run (p02: bodies and signatures AST-identical; only docstrings differ) | Identical bodies, and the same unit everywhere. |
| A4 | The refusal **allowance tail** (`store._raise_refusal` vs `store._request_refusal`; per-asker and per-worker caps) becomes one function returning a neutral tag that each caller maps to its own enum. | run (p03: the counting and cap statements match after alpha-renaming); read (the open-set filter line differs in 3 nodes and is equivalent by reading; the preambles differ entirely) | Only the tail is shared. The withdrawal predicates differ (`is_open` vs `ASKED`). **Keep withdrawal as two copies and pin them with a test.** |
| A5 | The strict JSON field decoders `_text`, `_number`, `_whole`, `_shown`, `_not_json` and `_BAD` are shared by `commandfile` and `sessionfile`; their `_file_bytes` differ and go to A2. `effects._quoted` and `bus._said` merge onto one function. They are *not* verbatim copies: `bus._said` calls `_one_line`. They agree on an 18-input corpus; pin that corpus before merging. | run (p04) | Verbatim decoders; behaviour-equal quoting. |
| A6 | **Break the lazy-import cycles.** `figures/host.py` (the imgui half) moves to `ui/typeset/`, and `app` passes fonts into `Renderer` instead of the `imscribe_fonts` default. The bus reply helpers move to a module that both `bus` and `commandbus` import. | run (the cycles); read (the fix) | T3: a module's requirements are visible in its imports. |
| A7 | **An import-layer test before any move.** It asserts the prose rules: board/fleet_view/pure modules import no imgui; store imports no disk shell; non-ui imports no ui; pure modules read no clock (T7). | doctrine T3 gap | This is the concept check. Every later move is then verified by it, not by eye. |
| A8 | **One `Pane` enum as the source of truth.** `PANE_MODULES` and `app._panels` derive from it. | read | T5, data-driven: three hand-kept registries become one table. |

### Survives with conditions: a design task first, then build

- **B1 `journal/`: the substrate half of `boardlog`** (append, read, `_name`, codec, `Clock`).
  `channels` already tunnels through it as `Unrecognised` and calls its privates. Board records,
  `boardfacts` and `boardsync` stay as the board's *instance*. Condition: prove byte-for-byte
  read-back of existing board and channel logs before and after.
- **B2 Atomic write primitives as *two* concepts**, `create_once` (link into a free name) and
  `replace`. Naming (fixed name vs counter), durability (only `commandfile` fsyncs the directory)
  and the collision policy are parameters. Each copy's docstring reasoning moves with it.
  `requests` is not a first adopter because it is unwired.
- **B3 One writer for `BoardWriter` + `ChannelWriter` only.** The two are near-copies; `drain`
  is identical (run, p09). The one behaviour difference is at `close` before the worker starts the
  oldest item: board reports "still queued", channels reports "may have begun". The shared writer
  adopts board's precise `_in_flight` close. That strengthens channels and needs its own test. It
  also takes over the restated `MARKER_REASON_CHARS`/`CLOSE_TIMEOUT` constants (channels.py:89-92),
  whose pin today covers only the boardsync side. `FleetWriter` (coalescing, retries) and
  `app.CommandFiles` (join-based close) stay out.
- **B4 Close `channels.outcomes`** with `assert_never` before any "consistent layout" copies the
  open form. Then move `app._record_commands` into a commands module beside the other three
  `outcomes`.
- **B5 Domain packages for the read side and the policy side.** `board/` (rows, graph, digest,
  dry-run, words) and a `gate/` package (approval's policy, `sandbox`, the pure `ui/gate_menu`
  ladder, `shellscan`). `command*` keeps its per-layer files, which the recorded build method
  depends on. It may sit under `command/` with no file merges. **`gate/` is the most contended
  move in the plan** (run, churn): 7 of the 10 most-changed files import `approval` (app, driver,
  model, store, inbox, detail, launcher). It goes last.
- **B6 Name the homeless concepts.** Inter-lead channels (model/store/effects/bus/app) gets one
  owning package. Id minting gets one owner: request-id prefixes `x-`/`k-`/`v-`/`q-` are reused
  across `bus.py` and `commandbus.py`. The fact that no prefix names these is the strongest
  evidence that prefixes are the wrong thing to steer by.
- **B7 `bus.py` as a table of tools.** `commandbus.Tool` plus the narrow `Roles` protocol is
  already the pattern. The ~3,000 lines of text renderers move to per-domain `*text` modules,
  following `commandtext`.
- **B9 `store._apply`.** The cleanest seam in the tree (Q 0.815) runs straight into a recorded
  reason: its single `match` is "the single audit point for every mutation" (store.py:451-452).
  **A per-entity dispatch table keeps one audit point; per-aggregate reducers do not.** Only the
  table form is proposed. It must not collide with the pending "H+ → P" store refactor
  (planning/2026-10-17-two-leads-in-one-window-share-a-channel.md:219).

### Refuted: do not build

- **Merging prefixed files.** It is unsupported by the coupling and works against the
  per-build-step file split that parallel boards depend on.
- **A four-way `SerialWriter`.** Its parameters (container, settle, latch, retry, close) are as
  large as the four instances. That is compression that relocates complexity rather than removing
  it.
- **An eleven-way `bound()`.** `review.head_lines` splits on `"\n"`, so `head_lines("a\n", 1) ==
  ("a", 1)`, and that count is what holds an approval (ui/review.py:268-278). A version built on
  `splitlines` returns `("a\n", 0)` and would release the hold (run, p01). It also answers
  differently for `"a\nb\n"`, `"\n"`, `""`, `"\r\n"` and `" "`. Pin those assertions even
  if nothing else is built.
- **A generic withdrawal** (see A4).
- **One walk over the task graph** (formerly B8; run, p08). cluster-board reported, by reading,
  that the five "walks over `Task.blocked_on`" differ only in their successor function. They
  differ in much more:
  - `store._cycle` does not walk `blocked_on` at all: it follows `depends_on` plus writers in any
    state. Executed: a COMPLETED dependency makes `_cycle` report a cycle that `blocked_on` and
    `why_blocked` cannot see.
  - `why_blocked` stops at HELD tasks, leaves and missing roots; `ready` keeps going (executed).
  - `ready` walks the edges in reverse.
  - The order is breadth-first in some walks and depth-first in others.
  - `_already_behind` is a client of `_cycle`, not a walk of its own.
  - `_on_a_cycle` walks a recorded sub-graph.

  A shared walk would need a start set, successors, an expand predicate, an order and an
  accumulator: the same parameter blow-up that refutes the four-way writer. EoP's own advice
  applies: "It is rarely the case that a single algorithm is appropriate in all circumstances."
- **A "persistence package" from community C3.** Its membership is a weighting artefact.
- **A global `core/` / `shell/` split.** Readers and the cross-module docstrings navigate by
  domain. Layer is expressed *within* a domain package and enforced by A7.
- **Restoring by replaying entries through the store.** This is already declined in `boardlog`'s
  docstring, with reasons.

## Rules any move must follow

1. **No re-export shims for names tests patch.** There are 859
   `monkeypatch.setattr(module_obj, "name")` sites, plus 53 dotted-string patches (39 of them on
   `driver`). A shim makes a patch land on the wrong module silently. Move the definition and every
   patch site in one commit.
2. `tests/conftest.py:605-607` (`boardsync.legacy_root`) and `test_command_view._ui_imports`
   (which reads `pptmstr/ui/{name}.py` by path) move with their targets. The skeptic weakened the
   isolation risk: `settings.config_dir` is redirected upstream for every unmarked test. It is not
   zero.
3. Persisted formats are keyed by explicit `KIND` strings, never class names, so a move does not
   migrate data. B1 and B2 still require a read-back proof.
4. Every move edits `app.py`, which imports every journal, and `app.py` is the most-changed
   file in the repo (run, churn: 53 commits since 2026-08-09; driver 38, ui/typeset/views 32,
   model 30, store 30). The prefix families themselves are cold, at 1–6 commits each. Branch work
   off main elsewhere lands in exactly the hot files. So order by churn: cold domains first, one
   domain per commit, rebased onto main before each round.


## Decided (operator, 2026-11-06)

- **D-a: the failure convention for shell writers.** Today there are three: return an outcome
  union (`boardlog`, `commandfile`, `sessionfile`), raise (`brief`), and swallow (`settings`).
  Each is argued locally, and none is in STYLE. Decided: add this line under STYLE.md §1.
  - "No Result/Either" means no generic wrapper.
  - A shell function whose caller must act on a refusal returns a domain-named outcome union.
  - One whose failure only needs showing raises.
  - One whose failure costs only a preference may swallow, and says so.

  This is consistent with STYLE.md:102-103 and 177-179. The STYLE.md edit is part of round 2.
- **D-b: citations across moves.**
  - `planning/archive/` stays as written apart from relative-link paths, which the archive's
    precedent already rewrites with `../`. Live planning files are updated.
  - When a module moves, live plans that cite it get a rename table appended in the same commit.
  - When a planning file is archived, code, tests and scripts that cite it get the path fixed in
    the same commit. That path fix is not archaeology: `theme.py:1291`, `ui/blocks.py:4` and
    `ui/span_layout.py:4` already cite `planning/archive/`.
- **D-c: every `outcomes` is a closed match.** `channels.outcomes` loses its `case _` and gains
  `assert_never` (B4).

## Environment

This was run while setting up this worktree. There is no `requirements-dev.lock`, so
`./bootstrap.sh` resolves fresh, and on 2026-11-06 it got versions newer than main's `.venv`:
- anthropic 1.14 → 2.6;
- claude-agent-sdk 0.2.167 → 0.2.178;
- pytest-asyncio 1.4.0 → 1.4.1.post1;
- the optional `merman` extra was left out.

On that set, pytest **fails at configure**. pytest-asyncio warns that
`asyncio_default_fixture_loop_scope` is unset, and `filterwarnings = ["error"]` turns that into an
INTERNALERROR. A fresh bootstrap today therefore yields a suite that cannot start.

This worktree's `.venv` is pinned to main's exact freeze instead, so the refactor's baseline stays
comparable. The fix touches dependency pins, so it is the operator's call:
- set the loop-scope option in `pyproject.toml`;
- cut the lock with `make lock`, which `2026-10-11-a-mac-install-is-a-lock-before-it-is-a-bundle.md`
  already owes.

## Build rounds

The operator approved these rounds. Each one ends with `make check` after every writer has
finished, compared against the baseline above.

**Round 1: pin, then fix the defects.** Each item gets a failing test first, then the fix. The
items share no files, so they run in parallel:
- the brief race;
- the boardlog symlink read (A2's first adopter);
- the launcher containment checks;
- the stale comments;
- the `head_lines` pins.

**Round 2: A7 first, then A1–A6 and A8.** This round also takes in:
- the D-a line for STYLE.md;
- closing `channels.outcomes` (D-c, from B4);
- deleting `scripts/mock_cards.py`, carried from 2026-08-10-layout-proposals, whose own docstring
  says to delete it.

Cold files go first and `app.py` last. Every move updates its patch sites in the same commit, with
no shims.

**Round 3: B1–B7 and B9 as design tasks.** Each produces a written design, not code, and starts
from its stated condition (for example B1's byte-for-byte read-back of existing logs). B5's
`gate/` goes last.

## Defects found on the way

These are not refactor scope. Round 1 takes the first four.

- **`brief.write_entry` loses entries** (run, p06).
  - The code calls `target.exists()` and then `os.replace`, with no fsync (brief.py:223, :235).
  - With synchronised writers it lost 1109 of 2000 entries using threads, 286 of 500 using
    processes, and 20 of 20 when the writers were forced to interleave. The last writer wins.
  - Its docstring (:202-206) says racing writers produce two entries, which is false.
  - The production path is two pptmstr windows appending to one session's brief directory. That
    was found by reading, not reproduced. No single-instance lock exists: `sessionfile.py:47` says
    "Nothing locks". The cross-window lock is still owed by
    `2026-10-13-a-fleet-that-survives-the-window.md`.
- **Symlinks are followed on record reads** (run, p05).
  - `boardlog.read` returns a record planted through a symlink as entry 0. commandfile,
    sessionfile and requests refuse the same setup.
  - Channel reads go through `boardlog.read`, so they are affected too.
  - `test_boardlog` has no symlink test. This is drift, not decision.
  - `channels._cannot_be_read` needs its own decision (see A2).
- **Containment checked by name: latent, and it fails closed** (run, p07).
  - `ui/launcher.py:2975` tests `policy is Policy.AUTONOMOUS`, bypassing
    `approval.requires_containment`. That function's docstring (approval.py:1058-1066) says the
    rule must not be checked by name.
  - All three current rungs agree with the rule.
  - A future rung that needs containment would launch with `containment=None`, and
    `driver.AgentSession.run` would refuse it at start (`UncontainedAutonomy`). The effect is a
    refused launch, not an uncontained one.
  - The launcher also tests the rung by name at `dangerous_engaged` (:2816) and `granted_policy`
    (:2835).
- **Stale or false comments.**
  - `shellscan.py:9-11` calls approval a small policy file.
  - `figures/__init__.py:2` says "pure", yet `figures.host` imports imgui.
  - `board._holder_left_behind` says the store cannot import board, but store.py:36 does. Probe
    p10 found no difference outside the documented narrowing across 27 cases.
  - `settings.py:45` cites `scripts/verify_mcp_status.py`, which does not exist.
  - `tests/test_shellscan.py:1571` cites a planning file that does not exist,
    `planning/2026-08-22-four-items.md`.
- **The Claude.ai login credential is still read on main.** `model_catalog.py:86`
  (`CREDENTIAL_PATH`) and `:154` (`raw.get("claudeAiOauth")`) read it. The unmerged
  `feat/packaging` branch removed that read on 2026-10-17 on terms-of-use grounds. This was
  flagged by the scrub review (read). It is not refactor scope; whether to cherry-pick the removal
  ahead of the branch is the operator's call.
- **`lies_inside` disagreement** (run, p11).
  - `tree.lies_inside_checkout` reaches its except branch on a symlink loop, a NUL or an unknown
    `~user`, and answers "inside" by design: it fails closed.
  - `worktree._lies_inside` reaches its except branch only on a NUL, which its one caller (paths
    from git stdout) cannot produce.
  - The disagreement is real but unreachable in practice.

## Planning scrub

This was run on 2026-11-06. `planning-status.csv` classifies all 74 entries under `planning/`:
30 built, 27 partly built, 10 decided, 5 live, 2 superseded. The groups recommended archiving 46.
A review cut that to **14 moved now** into `planning/archive/`, with their links rewritten. The
rest were deferred for one of two reasons:
- open work the groups had called done: orca, model-list, the two 10-12 records, ui-rework,
  imscribe drafts, which-fonts-ship and sub-agent spend;
- citations from code, tests, scripts, README, HANDOFF-advtext.md or notes/, which applies to 24
  entries. Each moves in the commit that fixes its citations (D-b).

Carry-overs written with the move:
- `t-rail-gate-display` → the fleet record;
- `max_budget_usd` unwired → the autonomous-mode record;
- the snapshot gate → the worktree record;
- `mock_cards.py` → round 2 above.

Still homeless: the lead-side amendable brief with acknowledgement. 2026-10-12 session-control
carries it until that file is archived, and after that it needs a new file.

## Open

- B1's byte-for-byte read-back of existing board and channel logs.
- `channels._cannot_be_read` with `O_NOFOLLOW`. A planted symlink is damage, not a transient
  failure, and that needs a decision and a test.
- Whether two windows really reach one brief directory in normal use. p06 named the path but did
  not reproduce it.
- feat/packaging. When it merges, it partly supersedes
  `2026-10-11-a-mac-install-is-a-lock-before-it-is-a-bundle.md` and finishes
  `2026-09-25-the-model-list-outlives-the-build-that-verified-it.md`.

## What was not run

Run in this round: the graph and its settling analyses, eleven probes, and the baseline
`make check`.

Gaps in what was run:
- p03's filter equivalence and p08's per-walk characterisation are partly read, not run.
- p07 used a stub session and a synthetic rung.
- The brief race was run in-process and cross-process, but not across two pptmstr windows.
- No probe ran on a second Python version or on macOS.
- `settle.py` has 24 mypy errors (loose dict typing). `make typecheck` does not cover it because
  it checks only `pptmstr/`.

The EoP quotes were read from the source. The Lo Russo, PAIP, lis.py, PEP 544 and cppreference
quotes are relayed.
