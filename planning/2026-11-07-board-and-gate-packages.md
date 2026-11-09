# The board's read side and the gate become packages, and the store imports only the gate's pure half

**Dated:** 2026-11-07 · **Status:** design only. Nothing is built. This is B5 of
[the refactor plan](2026-11-06-modules-are-named-for-what-they-require.md), round 3 (board task
`r3-b5`). It also finishes A1. · **Asked by:** the operator, through the round-3 build rounds
(premises `001-build-rounds.md`). · **Evidence:** all at **66af79e**, run 2026-11-07 with
`.venv/bin/python -I`:
- an AST scan of `pptmstr/`, `tests/` and `scripts/` for importers, imported names and patch sites;
- an SCC pass over the import graph, counting eager, lazy and `TYPE_CHECKING` edges;
- two probes of `approval.summary_of` / `summarize` with `Path.resolve` instrumented.

The scripts were in `$TMPDIR` and are not kept. The method is under "How the numbers were taken".
Churn is `2026-11-06-refactor-graph/churn.csv` (taken at b72a48a), plus `git log b72a48a..HEAD`.
Everything else is **read**.

## The answer in one paragraph

`approval.py` has more than a pure/disk split. Within it, three requirements are mixed:
- **leaf values** that `model` needs: `Policy`, the tool-name tables, `DiffText`;
- **pure composition** that needs `model` and `board`'s words: queue rows and provenance lines;
- **disk**: resolving a call's paths against the records roots, and reading an edit's target file.

`classify` and `summary_of` both sit on the disk side today, because both call `records_read` /
`records_written`, which `Path.resolve` every path (run). The store calls `summary_of`. It reaches
no disk only because the three tools it re-summarises are, **by tool name**, ones the records
checks return None for before resolving anything (run). Nothing pins that.

The leaves are what close the `{approval, board, model, templates}` import knot (plus
`command`, through a `TYPE_CHECKING` edge). Moving them out first therefore lets every other
piece import `model` at top level, and removes the `approval ⇢ board` lazy edge.

Proposed:
- **`board/`**: `rows`, `words`, `graph`, `digest`.
- **`addresses.py`**: the Snapshot-reading half of naming, which finishes A1.
- **`gate/`**: `policy`, `tools`, `diff`, `rows`, `reach`, `classify`, `park` and `sandbox`. The
  store imports only `policy`, `tools` and `rows`.

Two parts of the relayed `gate/` proposal are **declined**:
- `shellscan` stays top level. Its splitter is substrate for `command*`, and filing it under the
  gate would repeat `boardlog`'s mistake.
- `ui/gate_menu` stays in `ui/`. That follows `board.py`'s own precedent: a projection leaves
  `ui/` when it gains a reader outside `ui/`, and this one has none.

Order: `board/` first, then `addresses`, then `gate/` in two commits. `gate/` goes last because
seven of the ten hottest files import `approval`.

## (1) `approval.py` partitioned (read, 66af79e; two claims run)

`approval.py` is 1952 lines. Its top-level imports are `settings`, `commandtext.visible`,
`plugins.SKILLS` and `shellscan`. Its lazy imports are:
- `model.written_path` (:928);
- `board.{basis_label, incident_marker, property_label}` and four `model` names (:1383-1384);
- `model.stated_provenance` (:1590, :1626);
- `model.{Charter, Provenance, kept_provenance}` (:1683).

Every lazy import gives the same reason: `model` imports `approval`.

### The parts, by what they require

| Part | Members (approval.py lines) | Requires | IO |
|---|---|---|---|
| **policy** | `Disposition` :38, `Policy` :44, `requires_containment` :1054, `inherits_to_subagents` :1093 | nothing | none |
| **tools** | `_BUS_*` :129-136, `PEER_MESSAGE_TOOL`/`PEER_DECISION_TOOL` :141-142, `_AUTO` :147, `_REVIEW` :186, `_AUTONOMOUS_AUTO` :267, `_BUS_AUTO` :434, `_FILE_READS` :498, `_TREE_READS` :502, `_reviewed_by_arguments` :988, `peer_channel` :1009, `_dry_run` :1136, `states_provenance` :1603 | `Mapping` and a tool name | none |
| **diff (pure)** | `DiffText` :1753, `render_texts` :1855, `_unified` :1923, `diff_line_kind` :1938, `_FileState` :1886, `_edit_refusal` :1905 | strings | none |
| **rows (pure)** | `Summary` :1278, `_SUBJECT_KEPT` :1274, the body of `summary_of` :1312 apart from its first five statements, `command_shown` :1241, `_row_provenance` :1583, `_Refused` :1613, `_provenance_read` :1623, `_canonical` :1636, `is_edit` :1641, `edited_provenance` :1657, `declared_provenance` :1731 | `model` (Basis, stated_*, provenance), `board`'s four label words, `commandtext.visible` | none |
| **reach (disk)** | `_RECORD_ROOTS` :487, `_WRITE_ONLY_ROOTS` :495, `_PATTERN_CHARS`, `_READS_ONLY_ITS_OPERANDS`, `_MAX_DIRECTORIES`, `_Records` :526, `_place` :556, `_configuration` :572, `_records` :584, `_spellings`, `_fixed_part`, `_names_in`, `_shell_places` :664, `_named_places` :740, `records_read` :792, `records_written` :891 | `settings.config_dir`, `shellscan`, `model.written_path` | `Path.resolve` (realpath and lstat) on every named path; the config directory |
| **classify (disk, via reach)** | `classify` :1165, `_policy_admits` :1028, `_permissive_admits` :971, `_shipped_skill` :457 | policy, tools, reach, `shellscan`, `plugins.SKILLS` | via reach |
| **park (disk)** | `summary_of`'s records mark (:1395-1398) with `summarize` :1290 wrapped around rows; `diff_texts` :1786, `_read` :1892, `render_diff` :1872 | reach, rows, diff | resolve, as above; `Path.read_text` of an edit's target |

**The module docstring undercounts.** "Two things here touch the filesystem": it names
`render_diff` and `records_read`. It also omits:
- `records_written`, which resolves too;
- every caller of either, which includes `classify` and `summary_of`.

`tests/test_layers.py`'s `STORE_DISK_EXCEPTIONS` reason ("render_diff reads the file") inherits the
same narrowing. That is because its `_DISK_READS` counts `open`/`read_text`-shaped calls, not
`resolve`.

### What the store uses, and which part each lands in

`store.py:35` imports `Policy, Summary, peer_channel, summary_of`.

| Name | Store sites | Part |
|---|---|---|
| `Policy` | :514, :678 (default `STRICT`) | policy |
| `peer_channel` | :1837 `_peer_row`, :3344 | tools |
| `Summary` | :1874 `_summarised` | rows |
| `summary_of` | :1848 `_peer_row`, :1870 `_withdrawal_row`, both with no `session_id`/`cwd` | **rows and reach together today** |

**Run:** with `Path.resolve`, `settings.config_dir` and `approval._configuration` patched to raise,
`summary_of` composed rows for all three of the store's tools without raising. The three are
`send_to_peer`, `raise_decision` and `withdraw_peer_message`. The same function raised for
`Read /etc/passwd`. So the store's calls reach no disk today. The reason is that `records_read`
returns early for any name outside `_FILE_READS ∪ _TREE_READS ∪ {Bash}`, and `records_written`
returns early when `model.written_path` is None. That is a property of tool names that no test
states.

The split therefore gives the pure composer an explicit `mark: str = ""` argument. The store calls
it with no mark, and one new test pins that every tool name the store re-summarises:
- is outside the read-tool sets and is not `Bash`;
- has `written_path(name, …) is None`.

The second clause is checked for any arguments. That is checkable because `written_path` keys
on the name. If the pin ever fails, the store would be dropping a mark the driver drew, and the
test says so at the line.

The driver also composes these rows. It calls `summarize` with its `session_id` and
`_write_region()` (driver.py:2232). For the three peer tools that gives the same mark, `""`, so the
store's rewrite and the driver's row agree.

## (2) The packages

### `board/`: the read side, four modules

| Module | From | Holds | Requires |
|---|---|---|---|
| `board/words.py` | board.py :71-140 | `basis_label`, `property_label`, `not_run_label`, `incident_marker` | a `Basis` or a string. `Basis` is imported under `TYPE_CHECKING` only, since `basis_label` reads `.value`. **No runtime import from pptmstr.** |
| `board/rows.py` | the rest of board.py | `BoardTask`, `BoardConcern`, `BoardVersion`, `board_tasks`, `board_concerns`, `has_board`, `unrun_ledger`, `incidents`, the Snapshot-reading `*_label`s, `amender_name`, `UNDELIVERABLE` | `model`, `templates`, `addresses`, `words` |
| `board/graph.py` | boardgraph.py | unchanged | `model`, `rows`, `addresses` |
| `board/digest.py` | digest.py | unchanged | `model`, `rows`, `words`, `addresses` |

`board/__init__.py` is a docstring that lists the four modules and their few functions (T6), and
it re-exports nothing (Rule 1). Rule 1 bans shims. The panes stay in `ui/`: `ui/board_pane` and
`ui/digest_pane` draw the projection, and `ui` suffixes attach to their domain (the graph report:
`*_pane` cohesion 0.058).

**What stays out of `board/`:**
- `boardfacts`, `boardsync` and `boardlog`'s board records are the persistence half. B1 has them,
  as the board's instance of the journal.
- B5 must not pull them in. The raw-edge split (2 cross edges, both addressing) is the whole case
  for two halves.

### `addresses.py`: A1's remainder, and how the lazy edge goes away

Round 2 left `role_name` and `address_in_session` in `board`. They need a `Snapshot`, and
`naming`'s contract is pinned by `test_naming_imports_nothing_from_pptmstr`: "no import from
pptmstr". That contract exists because `model → approval ⇢ board → naming`, so any edge from
`naming` into the package closes a loop.

The fix has two parts.

1. **Remove `approval ⇢ board`.** The three names `summary_of` imports lazily are words: they need
   a `Basis` or a string and no `Snapshot`. Moved to `board/words.py`, which imports nothing at
   runtime, they can be imported at top level by whatever composes rows. In step 3 that becomes
   `gate/rows`. Before `gate/` exists it is a one-line change in `approval.py`: `from .board.words
   import …`, still lazy until step 4, because `model` still imports `approval`.
2. **Do not put Snapshot readers in `naming`.** Its requirement is "string maps", and the plan's
   thesis is to name a module for its requirement. A new `addresses.py` holds:
   - `role_name`, `_role_within_session`, `address_in_session`, `restored_table`, `_addresses`;
   - `UNKNOWN`, `FOREIGN` and `OPERATOR`, which `_role_within_session` names and `board/rows`
     reuses.

   It imports `model`, `naming` and `templates`. `boardfacts` and `board/rows` both import it, so
   the persistence half and the read half share a third module and no edge between them.

Once step 4 lands, `model` imports only gate leaves. `addresses → model` is then on no cycle, and
neither is `naming`. The SCC that holds `{approval, board, command, model, templates}` today (run)
then has nothing left in it, by reading: see refutation R2.

### `gate/`: eight modules

| Module | Holds (from §1) | Imports | Disk |
|---|---|---|---|
| `gate/policy.py` | policy | nothing from pptmstr | no |
| `gate/tools.py` | tools. Its tables go **public** (`AUTO`, `REVIEW`, `BUS_AUTO`, `AUTONOMOUS_AUTO`, `FILE_READS`, `TREE_READS`), because they become the module's interface: `classify`, `reach` and `rows` read them, and tests read `_BUS_AUTO`/`_REVIEW` 28 times. | nothing from pptmstr | no |
| `gate/diff.py` | diff (pure) | nothing from pptmstr | no |
| `gate/rows.py` | rows. `compose(tool_name, tool_input, width=90, *, mark="", reaches=None, across=None) -> Summary` is today's `summary_of` minus :1395-1398. | `model`, `board.words`, `commandtext`, `gate.tools` | no |
| `gate/reach.py` | reach | `settings`, `shellscan`, `model`, `gate.tools` | **yes** |
| `gate/classify.py` | classify | `gate.policy`, `gate.tools`, `gate.reach`, `shellscan`, `plugins` | via reach |
| `gate/park.py` | what the driver computes once, when a call parks: `summary_of` / `summarize` (the records mark from `reach`, then `rows.compose`), `diff_texts`, `_read`, `render_diff` | `gate.reach`, `gate.rows`, `gate.diff` | **yes** |
| `gate/sandbox.py` | `sandbox.py`, unchanged | nothing from pptmstr | env read only, as today |

`gate/__init__.py` is a table of contents and re-exports nothing.

The store imports `gate.policy`, `gate.tools` and `gate.rows`, all pure. `model`, `templates`,
`intents`, `sessionfile` and `fleetsync` import only `gate.policy`, `gate.tools` and `gate.diff`.
Those three are leaves, so `model`'s import of the gate cannot reach back into `model`. That is
what makes `gate/rows`' and `gate/reach`'s top-level `model` imports safe, and it deletes all five
lazy imports listed in §1.

**Comments whose reasons change with the move.** Rule: comments describe the code as it is.
- **:482-495, `_RECORD_ROOTS`/`_WRITE_ONLY_ROOTS`** are spelled again "because importing
  `sessionfile`/`brief` closes a cycle". After step 4, `gate.reach → sessionfile → gate.policy`
  closes none, by reading. So the stated reason becomes false. The move commit keeps the spelling
  and its test pin. A follow-up either imports `sessionfile.root()` and `brief.default_root()`, or
  restates the reason. The same roots are spelled a third time in `sandbox.denied_record_writes`,
  pinned separately. One table of records roots is a B6-shaped concept and not in B5's scope.
- **:125-128, `_BUS_SERVER`** is spelled to keep the SDK out. That reason still holds in
  `gate/tools`.
- **`DiffText`'s "defined here because `model` imports this module"** still holds of `gate/diff`.
- **`model.WITHDRAW_PEER_TOOL` (model.py:2852)** spells `approval._BUS_WITHDRAW_PEER` again, and
  `test_peer_surfacing.py:1483` pins it. Once `gate.tools` is a leaf, `model` can import it. That
  is a follow-up, not part of the move.

### Declined: `shellscan` and `ui/gate_menu` stay where they are

- **`shellscan`.** Its importers in `pptmstr/` are:
  - `approval`;
  - `command`, for `_split_segments`;
  - `commandtext`, for `_SEPARATORS` and `_split_segments`.

  The break-glass command domain splits and draws a command with the same parser the gate judged
  it by. That is deliberate: 2026-09-17 §3 says "the parser is the security property", and one
  parser is one reading.
  - Moving it under `gate/` files substrate under its first user. The plan names exactly that
    defect in `boardlog`.
  - Splitting the splitter out would put the parser and the table that depends on it in two
    files. That undoes 2026-09-17's reason for one reviewable page.

  It stays top level. `gate/classify` and `gate/reach` import it.
- **`ui/gate_menu`.** It imports no imgui and only `Policy` and `requires_containment`. Its two
  importers are `ui.health` and `ui.session_menu`.
  - `board.py:11-14` records the rule for this: a projection left `ui/` when a reader outside it
    appeared, and "it was in `ui/` while a pane was the only reader, which was true".
  - `ui/fleet_view` and `ui/projects` are imgui-free projections that stay in `ui/` on the same
    terms.

  `gate_menu` changes one import line, to `..gate.policy`.

## (3) What each move costs (run, 66af79e, except churn)

### Importers, by AST scan

"Statements" counts import statements. "Local" means function-local.

| Module now | Importing modules: pptmstr / tests / scripts | Statements: pptmstr / tests / scripts | `setattr(module, …)` in tests | Dotted-string patches | Churn (commits, csv + since) |
|---|---|---|---|---|---|
| `approval` | 22 / 36 / 8 | 23 / 60 / 8 | 1 (`test_typeset_diffs.py:403`, `diff_texts`) | 0 | 18 + 1 |
| `board` | 12 / 11 / 1 | 12 / 48 / 1 (43 local in total) | 0 | 0 | 6 + 2 |
| `boardgraph` | 1 / 2 / 0 | 2 / 2 / 0 | 0 | 0 | 1 + 0 |
| `digest` | 4 / 8 / 0 | 4 / 10 / 0 | 0 | 0 | 4 + 0 |
| `sandbox` | 1 / 8 / 7 | 1 / 10 / 7 | 0 | 0 | 3 + 0 |

`test_dotted_patches.csv` (53 rows, taken at b72a48a) has no row for any of these modules. The
fresh scan agrees: the one module-object patch is on `approval`. Rule 1's patch-site cost is
therefore one line for all of B5, and the cost is import lines.

### Other sites a move must carry

These are not patches.
- **Tests that read source by path, which must follow their code:**
  - in `test_approval.py`:
    - `test_no_preset_can_admit_a_tool_the_review_list_already_caught` (:913, AST of
      `approval.__file__`) → `gate/classify.py`;
    - `test_the_reducers_writing_tool_set_matches_summarizes_own_branch` (:1244) →
      `gate/rows.py`;
    - `test_the_comment_arguing_the_auto_approved_set_names_limits_and_questions_that_exist`
      (:1795, `inspect.getsource(approval)`) → `gate/tools.py`;
  - in `test_layers.py`, the `Claim`s for `approval` (:318), `board` (:320), `digest` (:322,
    :577), `boardgraph` (:573) and `sandbox` (:585);
  - `STORE_DISK_EXCEPTIONS["pptmstr.approval"]` (:489).
- **Tests reading private names:** `approval._X` 54 times. Of these, 48 are names `gate.tools`
  holds: 32 reads of the tables and 16 of the `_BUS_*` tool names.
- **Prose citations** (a grep for `module.name` and `module.py`; `board.` overcounts, because a
  local named `board` matches):

  | | approval | board | digest | sandbox |
  |---|---|---|---|---|
  | code | 67 | ≤60 | 25 | 17 |
  | tests | 205 | ≤255 | 26 | 19 |
  | scripts | 24 | 5 | 0 | 20 |
  | live plans (files) | 25 | 12 | 2 | 5 |

  D-b applies to the live plans: append a rename table in the same commit. The archive is left
  as written.

`test_settings._derivers` and `test_layers.modules` already walk `rglob("*.py")`. Hatch builds
`packages = ["pptmstr"]` with its subpackages, and mypy's only module override is `pptmstr.ui.*`.
New packages therefore need no configuration.

### Hot files touched

Top ten by commits: app, driver, ui/typeset/views, model, store, ui/inbox, ui/typeset/gate,
ui/detail, ui/transcript_pane, ui/launcher.

| Step | Hot files it edits | Others |
|---|---|---|
| 1 `board/` | store, ui/inbox (import lines) | approval (one lazy line), boardfacts, bus, effects, ui/board_pane, ui/decision_view, ui/digest_pane, ui/session_details, scripts/bench_panes |
| 2 `addresses` | store, ui/inbox | boardfacts, board/graph, board/digest, bus, ui/decision_view; prose in app, model, driver, naming |
| 3 `gate/` leaves | all seven: **model**, store, app, driver, ui/inbox, ui/detail, ui/launcher | templates, intents, sessionfile, fleetsync, ui/gate_menu, ui/health, ui/main_menu, ui/resume_form, ui/session_details, ui/session_menu, ui/settings_dialog, ui/typeset/diffs, ui/widgets |
| 4 `gate/` rest | **driver**, store, ui/inbox, ui/detail, ui/launcher | fake_driver, ui/review, ui/widgets, sandbox's importers, 8 scripts; `approval.py` deleted |

The task's premise is confirmed (run): 7 of the top 10 import `approval`. They are app, driver,
model, store, ui/inbox, ui/detail and ui/launcher.

### Order

Each step is one commit, rebased onto main first (Rule 4). `make check` runs after each and is
compared with the baseline.

1. **`board/`** (rows, words, graph, digest). It is the coldest set: 1–6 commits each, and none
   since round 2 except `board` twice. In the same commit `approval`'s lazy import becomes
   `board.words`.
2. **`addresses.py`.** This finishes A1. `test_nothing_reaches_a_moved_name_through_board` widens
   to cover these names.
3. **`gate/policy`, `gate/tools`, `gate/diff`.** This is what breaks the cycle. `approval.py`
   still exists and imports these three. It re-exports nothing, and every importer of the moved
   names is updated. The leaf names are most of the import cost. Eleven pptmstr modules import nothing from
   `approval` but `Policy`/`requires_containment`, and two import only `DiffText`. Doing the leaves first makes step 4 touch fewer files.
4. **`gate/rows`, `gate/reach`, `gate/classify`, `gate/park`, `gate/sandbox`; `approval.py`
   deleted.**
   - `rows` and `reach` turn their lazy `model` imports into top-level ones.
   - The store moves to `gate.rows.compose`, with the pin from §1.
   - `STORE_DISK_EXCEPTIONS` loses `pptmstr.approval`.

   Its stale check then forces the deletion, so the entry cannot be kept by mistake.
   This step is the most contended commit in the plan, so it lands last and alone.

A4 (deferred in round 2) edits `store._raise_refusal` / `_request_refusal`. It shares `store.py`
with steps 1–4 only at import lines, so the two do not conflict.

## (4) What would refute this

- **R1, the pure row is not the old row.** Before step 4 is committed, run a differential:
  `park.summary_of(n, a, session_id=s, cwd=c, reaches=r, across=x) ==
  approval.summary_of(...)` over every `(tool, args)` in `test_approval.py`'s corpora and the
  queue-row tests, with both `session_id` values. Any difference refutes the claim that the split
  moved code and changed nothing. The same applies to `classify`.
- **R2, the cycle survives.** After step 3, rerun the SCC pass and count lazy and `TYPE_CHECKING`
  edges. Any SCC containing a `gate.*`, `board.*`, `addresses` or `model` module refutes §2's
  claim that the leaves break the knot. The target graph was checked **by reading only**.
- **R3, the store's mark.** The pin in §1 fails if a peer tool ever becomes a read tool or a write
  tool. If it fails, `compose(mark="")` is wrong for the store, and the store has to take the mark
  from the parked record instead of recomputing it.
- **R4, the layer test cannot see the property it now carries.** `_DISK_READS` does not count
  `resolve`, `realpath`, `stat`, `exists`, `is_dir`, `settings.config_dir` or `ondisk.read_regular`.
  The last is round 2's open follow-up. Step 4 drops the approval exception, and as things stand it
  would do so because the store stops importing `approval`, not because anything showed that
  `gate.rows` touches no disk.
  - Widen the scan first, with a separate "touches the filesystem" set applied to the store rule.
  - Mutation-test it: put a `records_read` call back into `rows.compose` and confirm the store
    rule fails.

  Without this, the design's main claim is plumbed, not verified. `?.resolve` may also match
  methods that have nothing to do with paths, so calibrating it is part of the task.
- **R5, the declined moves.** `shellscan` belongs in `gate/` if `command` and `commandtext` stop
  importing its splitter. `gate_menu` leaves `ui/` if something outside `ui/` starts reading it.
- **R6, contention.** If `approval.py` gets commits on main between steps 3 and 4, step 4 is a
  rebase over moved code. In that case step 4 should be redone from the new main rather than
  merged.

## Found on the way

None of these are B5 scope.

- **The inbox's command row resolves paths on disk** (run). `ui.inbox.command_summary` calls
  `approval.summarize("Bash", {"command": …})` with no `session_id` or `cwd`. For any command
  `shellscan` admits, that runs `_shell_places`, which made **4 `Path.resolve` calls** each for
  `ls /tmp`, `cat a b` and `git status`, and 0 for `rm -rf x`.
  - By reading, `row_summary` feeds the queue's summary column. Whether that runs every frame was
    **not** run.
  - The row can also carry "a session's records ·", measured from pptmstr's own working directory.

  The split keeps this behaviour, because the inbox calls `park.summarize`. The open questions are
  whether a break-glass command row should carry a records mark at all, and whether it should
  resolve on the draw path.
- `ui.launcher` and `ui.widgets` call `classify`, which also resolves. How often they call it was
  not checked.
- `approval`'s module docstring undercounts its filesystem reach (§1). The move rewrites each
  module's docstring anyway.

## How the numbers were taken

- **Importers:**
  - Every `.py` under `pptmstr/`, `tests/` and `scripts/` was parsed.
  - Relative `ImportFrom` was resolved against the module's package, and an import was classed
    top-level, local or `TYPE_CHECKING` by where it sits.
  - A name counted as importing a module when it was the module itself (`from pptmstr import
    board`) or a name from it.
- **Patches:** `setattr`/`delattr` calls whose first argument is a local name bound to one of the
  modules, plus string first arguments `pptmstr.<module>.…`. This cross-checks with grep:
  `setattr(approval` 1, `setattr(board_pane` 35, `setattr(digest_pane` 9.
- **SCC:** Tarjan over every edge kind.
- **Probes:**
  - `Path.resolve`, `settings.config_dir` and `approval._configuration` were replaced with a
    raising stub, and `summary_of` was called for the three peer tools and for `Read`.
  - `Path.resolve` was wrapped with a counter around `summarize("Bash", …)`.

## What was not run

- No code was moved. `make check` was not run, because nothing changed.
- The target import graph, R2's acyclicity, was checked by reading only.
- R1's differential and R4's widened scan are designed and have not been run.
- Whether `row_summary` runs every frame was not checked.
- Churn is the b72a48a CSV plus `git log b72a48a..HEAD`. It was not recomputed.
- The prose-citation counts are grep upper bounds, not an AST count.
- Relayed by the task and checked:
  - approval is 1952 lines;
  - the store's top-level import;
  - the lazy `board` import at :1383;
  - 7 of the top 10 import `approval`.
- Relayed and narrowed: the read side does not take in the panes, and `gate/` does not take
  `shellscan` or `gate_menu`.

## Names moved out of approval.py

The text above cites these names in `approval.py`. B5's leaves step
(`planning/2026-11-07-board-and-gate-packages.md`, Order step 3) moved them to
`pptmstr/gate/`; the tool tables went public as they moved. A line number cited
above is `approval.py`'s when the text was written.

| Cited as | Now |
|---|---|
| `approval.Policy` | `gate.policy.Policy` |
| `approval.requires_containment` | `gate.policy.requires_containment` |
| `approval._AUTO` | `gate.tools.AUTO` |
| `approval._REVIEW` | `gate.tools.REVIEW` |
| `approval._BUS_AUTO` | `gate.tools.BUS_AUTO` |
| `approval._AUTONOMOUS_AUTO` | `gate.tools.AUTONOMOUS_AUTO` |
| `approval._FILE_READS` | `gate.tools.FILE_READS` |
| `approval._TREE_READS` | `gate.tools.TREE_READS` |
| `approval._BUS_SERVER` | `gate.tools._BUS_SERVER` |
| `approval._BUS_WITHDRAW_PEER` | `gate.tools._BUS_WITHDRAW_PEER` |
| `approval._dry_run` | `gate.tools._dry_run` |
| `approval._reviewed_by_arguments` | `gate.tools._reviewed_by_arguments` |
| `approval.DiffText` | `gate.diff.DiffText` |
| `approval._FileState` | `gate.diff._FileState` |
| `approval._edit_refusal` | `gate.diff._edit_refusal` |
| `approval._unified` | `gate.diff._unified` |

## Names moved by B5's last step

The text above cites these names in `approval.py` and `sandbox.py`. B5's last
step (`planning/2026-11-07-board-and-gate-packages.md`, Order step 4) split what
was left of `approval.py` into `pptmstr/gate/` (`rows`, `reach`, `classify`,
`park`) and deleted it, and moved `sandbox.py` to `pptmstr/gate/sandbox.py`
unchanged. A line number cited above is the old file's, when the text was
written.

| Cited as | Now |
|---|---|
| `_READS_ONLY_ITS_OPERANDS` (in `approval.py`) | `gate.reach._READS_ONLY_ITS_OPERANDS` |
| `_RECORD_ROOTS` (in `approval.py`) | `gate.reach._RECORD_ROOTS` |
| `_SUBJECT_KEPT` (in `approval.py`) | `gate.rows._SUBJECT_KEPT` |
| `_WRITE_ONLY_ROOTS` (in `approval.py`) | `gate.reach._WRITE_ONLY_ROOTS` |
| `_named_places` (in `approval.py`) | `gate.reach._named_places` |
| `_permissive_admits` (in `approval.py`) | `gate.classify._permissive_admits` |
| `_policy_admits` (in `approval.py`) | `gate.classify._policy_admits` |
| `_row_provenance` (in `approval.py`) | `gate.rows._row_provenance` |
| `_shell_places` (in `approval.py`) | `gate.reach._shell_places` |
| `_shipped_skill` (in `approval.py`) | `gate.classify._shipped_skill` |
| `approval._configuration` | `gate.reach._configuration` |
| `approval.py` | deleted; its parts are `pptmstr/gate/rows.py`, `reach.py`, `classify.py` and `park.py`, beside the leaves |
| `approval.summarize` | `gate.park.summarize` |
| `approval.summary_of` | `gate.park.summary_of`, which leads with the records mark and composes the rest with the pure `gate.rows.compose` |
| `command_shown` (in `approval.py`) | `gate.rows.command_shown` |
| `declared_provenance` (in `approval.py`) | `gate.rows.declared_provenance` |
| `diff_texts` (in `approval.py`) | `gate.park.diff_texts` |
| `edited_provenance` (in `approval.py`) | `gate.rows.edited_provenance` |
| `records_read` (in `approval.py`) | `gate.reach.records_read` |
| `records_written` (in `approval.py`) | `gate.reach.records_written` |
| `render_diff` (in `approval.py`) | `gate.park.render_diff` |
| `sandbox.denied_record_writes` | `gate.sandbox.denied_record_writes` |
| `sandbox.py` | `gate/sandbox.py` |
