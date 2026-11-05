# M2: S4 settled, and K vs H vs P judged from the count

Judgement for the 2026-10-17 plan's Decided 8, which is "under measurement". It builds on
[`m1-consumers.md`](m1-consumers.md) (now corrected in place, marked "Revised (S4 finding N; M2)")
and S4-shape-skeptic's 13 findings. The lead records the decision in the plan. This file
doesn't edit it.

Tags: **(run)** means I executed it and I'm reporting the output. **(read)** means I established
it by reading code at `e57b027`. Scratch is under `$TMPDIR/m2/` (`/tmp/claude-1000/m2/`) and is
not committed.

**Interpreter.** `/home/wreel/Source/pptmstr/.venv/bin/python` (3.11.2, mypy 2.4.0) with
`PYTHONPATH` set to this worktree. `pptmstr.__file__` printed this worktree's `pptmstr/__init__.py`.
**mypy ignores `PYTHONPATH`.** My first probe run without `MYPYPATH` produced nonsense errors
(`Mail? has no attribute "recipient"`). Every mypy result below sets `MYPYPATH` to the tree being
checked. `mypy -v` showed it parsing this worktree's `pptmstr/model.py`, and an unmodified copy
checks clean: "no issues found in 85 source files". The project's `[tool.mypy]` (`strict = true`,
`python_version = "3.11"`) came from `--config-file pyproject.toml`.

## The three designs, plus the one this measurement turned up

- **K:** a peer message is a `Concern` with `channel`/`needs_answer`/`re`. One mailbox: peers use
  `post_concern`/`read_inbox`, and `to` can name a channel.
- **H:** K's record and log (the `Concern` fields, and a widened `concern-posted`), but separate
  `send_to_peer`/`read_peer_messages` tools.
- **P:** a separate `PeerMessage` type, record kind and tools. It was measured two ways, because
  Decided 8 doesn't say which. **P1** puts `Concern | PeerMessage` in one `Snapshot.concerns` map
  ("every consumer `match`es over the two message types"). **P2** gives peers a map of their own.
- **H+** (named here): H in the store and the tools, but the log files a channel message under a
  **new record kind** (`peer-posted`) rather than a widened `concern-posted`. S4 noted that
  finding 6 "reduces to record kind vs field". Measuring it showed this is a separate axis from
  the store type: the log's kind and the model's type can be chosen independently.

## (a) S4's findings, settled

Needs-a-run item 2 was settled first, as the spec asked.

| # | S4 claim (severity S4 gave) | Settled | Evidence |
|---|---|---|---|
| 1 | P's `assert_never` covers none of K's 8 must-remember sites; they use attribute access (YES) | **Partly.** Right about the mechanism, wrong that it covers nothing. See the note below | run: `$TMPDIR/m2/probe/union_probe.py`, and a scratch copy of `pptmstr/` with `Snapshot.concerns` widened |
| 2 | The `post_concern` and `read_inbox` tool descriptions become false under K only; `test_wake_asker.py:484` is a missed D test (YES) | **Accepted.** Both descriptions are true under H, H+ and P. Two K-only silent sites | read: `bus.build_server`; run: `m1_sites.py --m2` finds `:484` |
| 3 | `_concern_audience_lines` is gate/approval D under K (MAYBE→YES) | **Partly.** It is D and silent, but under **all** designs: the sentence says "Messages between agents", and a peer message is one under H and P too. It cancels. `test_templates.py:718` is a missed D test | read; run: `--m2` finds `:718` |
| 4 | Roles and channels share one namespace in `to`: two remember-yes sites, K only (MAYBE) | **Accepted.** `resolve_role` checks `ROOT_ADDRESSES` then `_roles`; `_address_for` allocates later. Not present under H, H+ or P | read: `driver.py:1456`, `:1555` |
| 5 | "Only leads send" is enforced nowhere (NO) | **Accepted**, same under all designs. Every worker's `tool_list` appends `BUS_TOOL_NAMES`, and `tools=None` inherits everything | read: `templates.Role.tool_list` |
| 6 | The older-build case is routine here, and its downstream effect is worse than M1 said (MAYBE) | **Accepted.** It is the hazard that separates H from H+ and P. See §(b) note 3 | run: `config_dir()` from five checkouts; `$TMPDIR/m2/hplus/probe_hplus.py` |
| 7 | Claim (e)'s restore misfiling does not weigh for P (NO) | **Accepted.** A P arm calls the same `node_of`, and the guard is one line under every design | read: `store._restored.node_of`, `boardlog._party` |
| 8 | M1 least-sure (d): no production emitter of `ConcernEdited`/`ConcernWithdrawn` (NO) | **Accepted, and history confirms it.** Only `135f917` added an emitter, and it is in `fake_driver.py`. `eccaa24`/`a6b3dea` add arms only | run: `git log -S` (needs-a-run 3) |
| 9 | "task_id ambiguous across boards" is weaker than stated; `test_board.py:423`/`:827` become D under a recipient-task rule (MAYBE) | **Partly.** The first half is accepted: ids are global in one store. The second half is refuted for a peer-only rule, because both tests post same-session mail, so only a global rule flips `:423`. It is a rule choice, fixed in §(d) | read |
| 10 | `WakeReason`/`wake_line` are the same edit under K and P (NO) | **Accepted.** They count under neither side | read |
| 11 | The script misses in-module boardlog names (NO) | **Accepted.** `--m2` adds `boardlog.py:454` (`Known`) and `_named`'s two arms. These are I under K/H and forced arms under H+/P | run |
| 12 | The classification spot-check holds except `_concern_audience_lines` (NO) | **Accepted**, with one exception. S4 marks `fleet_view._kept` as a silent omission under P. **That is refuted:** `fold` uses `_kept` only to keep *sub-agent* cards, and a peer message is root to root | read: `ui/fleet_view.fold` |
| 13 | Claims (a), (b), (c) hold (NO) | **Accepted** (not re-run). The 13-line wake sketch rests on where the link record lives, which is still open | relayed from S4 and M1, spot-read |

S4's needs-a-run list, item by item: 1 (P's omission count) is in §(b), run by grep and settled by
reading. 2 is finding 1. 3 is finding 8. 4 is `m1_sites.py --m2` (findings 2, 3, 11). 5 is finding 6.

**Finding 1 in detail (run).** These are `mypy --strict` results with the project settings, on a
minimal `PeerMessage` that shares `Concern`'s field names:

| Case | mypy |
|---|---|
| A: `inbox_of`-style attribute access, return type widened to the union | **nothing** |
| B: the same body, return left as today's `tuple[Concern, ...]` | error: "Generator has incompatible item type" |
| C: union element passed to a function typed `Concern` (like `_concern_wake`) | error: arg-type |
| D: a field only `Concern` has (`task_id`), read over the union | error: union-attr |
| E: `match` with the `PeerMessage` arm missing, then `assert_never` | error |
| F: a separate `peers` map that a consumer never reads | **nothing** |
| G: `isinstance(c, Concern)` with the peer branch falling through | **nothing** |

The same experiment was run on the real tree. In a scratch copy of `pptmstr/` I added a
`PeerMessage` and widened `Snapshot.concerns` to `Mapping[ConcernId, Concern | PeerMessage]`.
mypy then reports **9 errors in 3 files** (with `task_id` on `PeerMessage`) or 13 in 4 (without
it). They are at `model.inbox_of`, `store._apply` `InboxRead`, `store` `BoardRestored`, `store._restored`
(×2), `boardfacts._filed` (×2) and `boardfacts._changed` (×2), plus `board.board_concerns`/
`_open_concerns_by_task` ×4 when `task_id` is absent. I then gave every flagged annotation the
quickest fix, widening it to the union. Two rounds and 8 edits made the tree check clean. The
only extra error the widening exposed was `bus.read_inbox`'s call to `_operators_notice`.

What that shows:
- **Not pointed at under P1:** `_concern_wake`, `post_concern`, `resolve_role`, `role_status`,
  `role_of`, and `read_inbox`'s rendering of the sender ("another agent"). That is 6 of K's 8 D
  sites. The remaining two, `board_concerns` and `_open_concerns_by_task`, are pointed at only
  when `PeerMessage` has no `task_id`. S4 is right about this.
- **Pointed at, but the quickest fix is silently wrong (P1):** the widened `boardfacts._filed`
  builds a `boardlog.Posted` from the peer and type-checks. That files a peer message as an
  ordinary `concern-posted`, which **reproduces K's field-vs-kind hazard in the new build**. The
  widened `inbox_of` hands peers to `read_inbox`. mypy points at both lines, and the reviewer
  then has to choose the right fix.
- **Where S4 is wrong:** "P's only real protection is at functions typed `Concern`". In this tree
  that is not a small set. `inbox_of`, the restore, `_filed`, `_changed` and `InboxDelivered` are
  all annotated `Concern`, so P1 does point at the store/log path. It doesn't point at the
  addressing, display and wake path.

## (b) Corrected counts

Only sites that differ between designs are listed. The rest of the 152 production sites (the
143 found by `m1_sites.py --m2`, 7 found by reading in M1, and `_address_for` and
`BUS_TOOL_NAMES` found by reading here) are I under K, H and H+. Under P they are N: a peer never
reaches them, and their parallel is new code. Classes follow M1's rubric. D sites carry how they
fail when forgotten: **silent** (wrong output, no error, nothing existing fails), **safe** (the
peer feature visibly doesn't happen), **loud** (refusal or error), or **forced** (mypy points at
it).

| Site | K | H | H+ | P2 (separate map) |
|---|---|---|---|---|
| `bus.read_inbox` rendering | D **silent** | I (once `inbox_of` excludes peers) | I | N |
| `driver.role_of` | D **silent** (same path as above) | N | N | N |
| `post_concern` @tool description | D **silent** | I | I | I |
| `read_inbox` @tool description | D **silent** | I | I | I |
| `driver.resolve_role` namespace | D **silent** (finding 4) | N | N | N |
| `driver._address_for` | D **silent** (finding 4) | N | N | N |
| older build reads the log (not a site) | **silent** | **silent** | loud (short) | loud (short) |
| `model.inbox_of` | I (peers wanted here) | D **silent**: must exclude channel mail, or `read_inbox` delivers it as "another agent" and marks it read | D **silent** (same) | N |
| `boardfacts._filed` | F | F | D **silent**: must pick `peer-posted` by `channel`; forgotten, a restart turns the peer into a concern | N |
| `store._restored` placement loop | I | I | I | D **silent**: peers in `Board` are never placed, so they vanish on resume |
| `digest.digest` incidents | I | I | I | D **silent** (minor): a marked peer is missing from the sender's digest |
| `app._newest` | I | I | I | D **silent** (cosmetic): "written up to" ignores peers |
| `ui/widgets._GATE_PROBES` | I | D silent (cosmetic) | same | same |
| `bus.post_concern` routing, `resolve_role`, `role_status` | D loud | N | N | N |
| `store._concern_wake` | D safe | D safe | D safe | new arm, forced |
| `board.board_concerns` | D safe | D safe | D safe | N (P keeps peers off boards) |
| `approval._REVIEW`, `bus.BUS_TOOLS` | I | D loud (an unlisted tool parks on every rung; an unstamped one raises) | same | same |
| `ui/inbox.DESCRIBED`, `approval.summarize` | I / F | D visible-degraded (generic card, body not editable as prose) | same | same |
| `boardlog` `Known`, `_named`, `restore` arms | I / F | I / F | forced (new kind) | forced |
| `_apply`, `boardfacts.outcomes`, `fleetsync.changes` arms | I | forced if a new read intent | same | forced (new intents) |
| `WakeReason`, `wake_line`; `lead_briefing`; `_concern_audience_lines`; only-leads refusal | same under all: forced; silent; silent; silent | ← | ← | ← |

**Silent, unforced, design-specific hazards** (the same-under-all row cancels):

| | K | H | H+ | P1 | P2 |
|---|---|---|---|---|---|
| count | **7** | **2** | **2** | **3** (+2 pointed at, where the quickest fix is silently wrong) | **3** (1 data loss on resume, 2 minor) |
| which | read_inbox, role_of, 2 descriptions, namespace ×2, old build | inbox_of, old build | inbox_of, _filed | P2's three (P1 restores through the same placement loop) | placement, digest, _newest |

**Revised (S5; M3):** the H+ and P2 cells of this row are recounted, site by site and by run, in
"Revised (S5; M3)" at the end of this file. The cells above are M2's and are left as M2 wrote them.

`_GATE_PROBES` is excluded from the row because it is equal across H, H+, P1 and P2. P1 is
shown for completeness. It carries P2's omissions and adds the two traps found under finding 1.

**Notes.**
1. M1's "K 8 must-remember vs P 0" is withdrawn on both sides. K's design-specific silent count
   is 7, not 2. P's is 3, not 0.
2. H does **not** remove the `read_inbox`/`role_of` pair for free, as S4 said. The pair moves to
   one site, `inbox_of`, which has to exclude channel mail. If it doesn't, the message is
   delivered and marked read through the wrong tool, so `read_peer_messages` never sees it.
3. **Finding 6, weighed (run).** `settings.config_dir()` depends only on `XDG_CONFIG_HOME` and
   `$HOME`. Imported from this worktree, the main checkout, `ui-rework`, `feat-packaging` and
   `feat-hybrid-permissions`, it printed `/home/wreel/.config/pptmstr` every time, and
   `sessionfile.root()` printed `.../sessions` every time. Every build lists and resumes every
   session from there. `boardsync`'s docstring already designs for "a build that predates the
   move resuming a session this build started". So an older build reading a newer log is an
   ordinary event, and **every build on this machine today is older than any peer feature**.
   Under K and H, such a build restores a peer message as a delivered ordinary concern with
   nothing reported, and its `read_inbox` writes the `Delivered` that starts Decided 6's
   obligation for a reader that never saw `needs_answer`. Under H+ and P it restores short:
   the message isn't placed, and its delivery record is `stray` (probe output in m1-consumers.md
   claim (d), revised). No later edit in this tree can repair the misread. It lives in builds
   that already exist.

## (c) The threshold, stated before it is applied

**Disclosure.** I wrote this after the runs above and before tallying the table into a verdict.
H+ was named after reading the log-kind evidence. Each rule below comes from STYLE.md and not
from which design it favours.

1. **The measure is silent, unforced, design-specific hazards.** These are sites that must
   change for a peer, that mypy does not point at (as measured, not as a `match` could make it),
   and that fail silently. STYLE §1, "Derive; do not store": "whoever remembers is where this
   codebase's defects have historically come from". "Sum types, not base classes" earns credit
   only where the type check actually fires. Loud and safe failures don't count, and neither do
   forced sites. Hazards that are the same under every design cancel.
2. **A silent misread by another build that routinely reads the same log counts as one
   hazard, and it cannot be repaired later**, because it lives in builds that already exist. The
   reason is the boardlog contract ("the format only grows") and Decided 5's reasoning.
3. **A design wins outright only by a gap of ≥ 2.** A gap of 1 is within the noise this
   measurement has shown: S4 moved M1's silent count by 5, and I moved S4's by refuting one
   site. Within a gap of 1, ties break in this order:
   - (i) against any design carrying rule 2's unrepairable hazard;
   - (ii) toward fewer new parallel structures. Each is a STYLE §3 "deliberate duplication"
     needing a pin test, and a second path the model must be told the purpose of (§3, "a
     channel that duplicates a path");
   - (iii) toward fewer sites touched.

## (d) The judgement: H+

The threshold applied to the §(b) row (K 7, H 2, H+ 2, P1 3, P2 3):

- **K is out.** 7 against at most 3 is a gap of ≥ 4. Most of K's hazards are prose and addressing
  that no type can carry: the two tool descriptions and the role/channel namespace. Separate
  tools remove all of them, as Decided 8 already says for the namespace.
- **H, H+, P1 and P2 are within a gap of 1, so the result is a tie.**
- **Tie-break (i) removes H.** It carries finding 6's unrepairable misread, and the others don't.
- **Tie-break (ii) picks H+ over P1 and P2.** H+ has two parallels: two read projections over one
  map, and two posted kinds in the log. P adds a type, two intents with their arms in three
  reducers, a restore placement path, a `Board` field, a digest slot, and twins for every
  envelope test. That is M1 (e)'s list.

**So the judgement is H+.**
- `Concern` gains `channel`, `needs_answer` and `re`, with defaults that claim nothing.
- Peers use their own `send_to_peer`/`read_peer_messages`.
- `inbox_of` and a new `peer_inbox_of` partition `snap.concerns` on `channel is None`. That is one
  derived predicate, and one test pins that the two partitions are disjoint and cover every
  waiting concern.
- `boardfacts._filed` writes a channel message as a new `peer-posted` kind, which an older build
  reads as `Unrecognised`. A round-trip test pins it: "a concern with a channel restores with
  its channel".
  **Revised (S5 finding 1; M3), run.** It does not pin the kind. With `Posted` widened by a
  `channel` field instead of a `peer-posted` kind, that round trip passes and an older build reads
  the record as an ordinary delivered-to-inbox concern. The pin that does is in
  "Revised (S5; M3)" below, and the round trip is still needed beside it, because it is the one
  of the two that catches `store._restored_concern` dropping `channel`.
- `task_id` on a peer message names the **sender's** task. That rule needs no edit
  (`_open_concerns_by_task` is sender-scoped) and leaves `test_board.py:423`/`:827` as they are
  (finding 9).

**This narrows Decided 8 and reverses part of it.** It keeps Decided 8's tools and its "new
kind, so an older build notices", because that argument was about the log, and the log keeps it.
It reverses "its own record type" in the store. The measurement found that the store type buys
mypy coverage only on the store/log path (finding 1). H+ protects that path by the record kind
and two pin tests. It does not buy coverage on the addressing, display and wake path, where the
hazards were.

**What would reverse it:**
- **To P:** ~~a recount finding ≥ 2 more H+-only silent sites than P-only ones. One would make the
  gap 2 against H+, under rule 3.~~ **Revised (S5 finding 2; M3):** a recount finding net **≥ 3**
  more H+-only silent sites than P-only ones. From H+ 2, P 3, net +2 gives H+ 4 vs P 3, a gap of 1
  against H+, which is still a tie under rule 3; net +3 (H+ 5 vs P 3) is the first gap of 2. The
  threshold itself (a gap of ≥ 2) is unchanged. See "Revised (S5; M3)" below for the recount.
  The likeliest source is existing code that must treat a
  channel `Concern` differently and only reads `.recipient`/`.state`. The new consumers (the
  needs-answer count, Decided 4's age rule, the DIGEST thread) are written knowing about peers,
  so they don't count.
- **To H:** the operator ruling that an older build misreading a newer log doesn't matter, for
  example by giving each worktree its own `XDG_CONFIG_HOME`. Rule 2 then falls away, H and H+
  tie, and H is cheaper.
- **To K:** a ruling that peers must arrive through the one `read_inbox`. That brings back the
  description and namespace hazards, and K would have to be re-measured with a structural
  `channel:` prefix in `to` (S4 finding 4), which nobody has costed.

## (e) What each costs here, and how each becomes another

These are rough counts of existing sites touched, from §(b) and M1 (e). New code that every
design needs isn't counted: the link lookup, needs-answer card count, age rule, DIGEST thread.

| | Existing sites touched (approx.) | New parallels needing a pin |
|---|---|---|
| K | ~17: the 10 F sites, post_concern, resolve_role, role_status, read_inbox, role_of, _address_for guard, two descriptions plus `test_wake_asker.py:484`/`:148`, plus the same-under-all four | 0 |
| H | ~25: the 10 F sites, inbox_of, peer_inbox_of, two handlers, _REVIEW, BUS_TOOLS, DESCRIBED, summarize, _GATE_PROBES, a read intent's three forced arms, plus the same-under-all four | 1 (the inbox partition) |
| H+ | ~31: H, plus the record class, `Known`, `_named` and `restore` arms, the `_filed` branch, and the generator in `test_boardlog.py` | 2 (partition, posted kinds) |
| P2 | ~45: a type, two intents with arms in `_apply`/`outcomes`/`fleetsync`, record kind(s) with `Known`/`_named`/`restore`, a `Board` field and its restored type, the `_restored` placement and its `_timeless` twin, a digest slot, _newest, the five approval/tool surfaces, plus the same-under-all four | ≥ 3, plus twins of envelope tests (unmeasured) |

**Moving between designs, within "the format only grows":**
- **K → H:** tools only, with no format change.
- **H → H+:** add the kind and file new messages under it. Messages already written widened stay
  misread by older builds forever. That is the cost of starting at H.
- **H+ → P:** a store refactor only. The log already files peers under their own kind, so the
  format doesn't change. `peer-posted` restores into `PeerMessage` rather than `Concern`. This
  is why H+ is the no-regret start: the log is already P's.
- **K/H → P:** P has to read widened `concern-posted` records with a channel forever, as peers.
  That is a permanent second restore path.
- **P → H+/K:** a restore that folds `peer-posted` into `Concern`. It is cheap, and the kind stays
  in the format forever, which is harmless.

## (f) Do `needs_answer`/`re` belong to concerns too?

**On the record, yes. On `post_concern`, not now.**
- Under H+ the fields are on `Concern`, so a team concern has them at their defaults (`False`,
  `None`). Those claim nothing, and nothing reads them for a channel-less concern.
- Giving `post_concern` the arguments would widen the tool every worker sees. It would also put
  Decided 4's NEEDS YOU age rule on in-team mail, which has its own wake (worker to lead) and its
  own escalation (`raise_decision`). Nothing measured here says a team needs either.
- That is STYLE §3's "a channel that duplicates a path the model already has". Revisit it if the
  DIGEST thread view is built for peers and someone wants it for concerns. The `re` field would
  then already be there, with no format change.
- One constraint holds either way: peer-ness is derived from `channel is not None` and never
  stored as a second flag. A cross-session concern with no channel is refused at restore~~, which
  is finding 7's guard~~.
  **Revised (S5 finding 5; M3), read.** That is not S4 finding 7's guard. Finding 7's guard is
  "a Posted with channel set and no recipient_id is NotPlaced": it refuses an *address-only peer
  recipient*, so `store._restored.node_of` cannot misfile it. The sentence above proposes a
  different, new guard, and it contradicts the tree as it stands: `store._restored_concern`'s
  docstring places a recipient in another session "like any other", and
  `tests/test_boardfacts.py::test_a_log_restored_into_another_process_is_the_board_the_first_one_held`
  generates cross-session concerns (`_AGENTS` spans sessions S and Z) and asserts
  `report.unplaced == ()`, so that guard as worded would turn it red. Neither guard exists in the
  tree. Neither makes `_restored_concern` dropping `channel` loud (§ "Revised (S5; M3)", A): a
  guard that reads `logged.channel` passes, and the `Concern(...)` built after it still drops the
  field.

## What was not run

- No design was built, and no pptmstr test was run. The cost column in (e) is estimated by
  reading.
- P1's "quickest fix" path is one plausible sequence of edits. A different reviewer could fix the
  flagged lines differently.
- The P2 omission list comes from `grep -nE "\.concerns\b|inbox_of\("` plus reading. Sites that
  reach concerns through a projection were taken from M1's chain reading and not re-traced.
- The H+ probe used this build's `boardlog` as the "older build". No actual older-branch build was
  run against a peer record.
- M1's claims (a)–(c) were not re-run (finding 13).

## Revised (S5; M3)

M3 ran S5's needs-a-run items A, C, D and E, plus the P2 side of each count, with
[`m3_checks.py`](m3_checks.py). B is not run (see below). This section revises the H+ and P2
cells of §(b)'s count and the arithmetic in §(d). It does **not** change the threshold in §(c),
and it does not pick a reading of tie-break (i). The operator chooses the reading.

**How it was run.** `/home/wreel/Source/pptmstr/.venv/bin/python -B m3_checks.py WORKTREE`
(Python 3.11.2, mypy 2.4.0). Each variant is a copy of `pptmstr/`, `tests/` and
`pyproject.toml` under `$TMPDIR/m3/trees/<variant>` (`/tmp/claude-1000/m3/`), with literal
patches that must each match exactly once. Each probe runs in a child with `PYTHONPATH` set to
the tree under test and prints `pptmstr.__file__` first. Every probe line in the output names
`/tmp/claude-1000/m3/trees/<variant>/pptmstr/__init__.py`, and the older-build reads name this
worktree's `pptmstr/__init__.py`. mypy runs with `cwd` and `MYPYPATH` both set to the scratch tree
and `--config-file pyproject.toml`. The unmodified copy checks clean ("no issues found in 85
source files"). pytest runs as `python -m pytest -p no:cacheprovider` from the scratch tree's cwd
with no `PYTHONPATH`, and the import line printed beside each run names the scratch tree.
Bytecode is off in every child. Nothing was written in the worktree but the two files of this
task.

**Scratch shortcuts that limit what the runs show.**
- `peer-posted` is a scratch `PeerPosted(Posted)` subclass with its own `KIND`, chosen in `_filed`
  by `channel is not None`. It inherits every `case Posted()` arm. A real H+ would add arms of
  its own.
- P2 is a minimal scratch: a `peers` tuple on `boardlog.Board` and a `peers` map on `Snapshot`,
  with nothing reading either. That is exactly the hazard being tested (an existing reader that
  never reads the new collection), not a P2 build.
- The "older build" is this worktree, unmodified, as in M2. No other branch was run.

### The runs

**A: `store._restored_concern` (S5 N1) and `model.inbox_of`.**

| Variant | mypy | Result |
|---|---|---|
| `Concern.channel = None` only (no partition) | clean | a live channel concern A→B **is** in `inbox_of(B)` |
| H+: `channel` on `Concern` and `RestoredConcern`, `inbox_of` partitioned, `_restored_concern` unchanged | clean | live: not in `inbox_of(B)`. **Restored: `channel` is `None`, and it is in `inbox_of(B)`. The report has `unplaced == ()` and `short == False`** |
| the same, with `_restored_concern` copying `channel` | (not re-run) | restored `channel == 'x'`, not in `inbox_of(B)` |
| `channel` keyword-only with **no default** | **4 errors**, `call-arg` | `model.py:2473` (`closing_notice`), `store.py:2994` (`_restored_concern`), `fake_driver.py:1033`, `bus.py:3002` |

N1 is **confirmed silent** as measured: no type error, nothing reported, and the peer reaches
`read_inbox` after a resume. `inbox_of` is **confirmed silent** too: without the partition, mypy
is clean and the peer is in the team inbox.

**C: what `_filed` writes, and what each pin catches.**

| Variant | mypy | kinds written | M2's round-trip pin | kind pin | older build reading the records |
|---|---|---|---|---|---|
| widened `Posted` (`channel` on `concern-posted`, copied through restore and store) | clean | `concern-posted` | **PASS** | **FAIL** | decodes `Posted`, board not short, **placed, and in `inbox_of(B)`** |
| `peer-posted` kind, store copies | clean | `peer-posted` | PASS | PASS | decodes `Unrecognised`, board short (`unrecognised ['peer-posted']`), not placed |
| `peer-posted` kind, store does **not** copy | (not run) | `peer-posted` | **FAIL** | PASS | same as the row above |

M2's round-trip pin here runs end to end: `ConcernPosted`, `boardfacts.outcomes`, `encode`,
`decode`, `boardlog.restore`, then `BoardRestored` into an empty store, asserting
`channel == 'x'`. It cannot see the kind. The kind pin cannot see the store. **Each catches what
the other misses, so H+ needs both.** `_filed` is **confirmed silent and rule-2 class**: the
widened mistake type-checks, passes the round trip, and every older build misreads the record.

**D: every `Concern(` construction in `pptmstr/` (by AST).**

| Site | Copies `channel`? |
|---|---|
| `bus.py:3002`, in `build_server` (the `post_concern` handler) | No. It is the team path, and `None` is right. `send_to_peer` is a new handler written for peers |
| `model.py:2472` `closing_notice` | No. Operator to holder, in-team |
| `fake_driver.py:1033` `_seed_board` | No, unless the demo board is to show peer mail |
| `store.py:2994` `_restored_concern` | **Yes**: the only existing construction that must copy it |
| `boardlog.py:1257` `RestoredConcern(` in `restore`'s `Posted` arm | Only under the widened-`Posted` mistake. Under H+ the `peer-posted` arm is new code, and it sets the field |

This confirms M1 claim (c)'s set (relayed until now). `dataclasses.replace` sites keep the field
on their own, so they don't need it.

**E: does the field walker reach `store`?** No. `tests/test_boardlog.py` does import
`pptmstr.store`, but only inside
`test_a_handed_version_is_bounded_from_one_where_the_claim_recorded_none` (`:1875`). With
`store._restored_concern` patched to raise, the walker passes (2 passed), so reading its imports
alone would not have settled this. Two more runs change what N1 means:
- The walker **fails** as soon as `Concern` gains `channel`: "a board fact with nowhere to go",
  `{'channel'}`. So adding the field is loud at the boardlog layer. Either fix (widened `Posted`
  or `peer-posted`) satisfies it, so it pins neither the kind nor the store.
- A store-reaching round trip does exist:
  `tests/test_boardfacts.py::test_a_log_restored_into_another_process_is_the_board_the_first_one_held`.
  With `_restored_concern` raising, it fails. With `peer-posted`, `_restored_concern` **not**
  copying, and the generator `_concern` unchanged, it shows **40 passed**. Make the generator set
  `channel` and it fails. Make the store copy too and it shows 40 passed. Its generator builds
  `Concern` by keyword, so a new field takes its default unless someone remembers it, and N1
  stays silent until they do.

**P2's three sites.** p2 scratch: mypy is clean.
- **Placement loop: confirmed silent.** A board with one peer in `peers` restores to nothing.
  The report says `unplaced == ()`, `short == False`, `concerns == 0`.
- **`app._newest`: confirmed silent.** It returns `None` for that board, and `7.0` when the same
  record is in `concerns`.
- **`digest.digest` incidents: confirmed silent.** An incident-marked peer in `Snapshot.peers` is
  0 of the digest's incidents, and 1 when it is in `concerns`.

**B (`bus._withheld`, S5 N2): not run, conditional.** It exists only if `send_to_peer` accepts
`incident`, which is undecided. It is counted below only in the "peer `incident`" rows, on S5's
reading.

### The recount

Silent, unforced, design-specific sites, by §(c) rule 1 and confirmed as above:

| | H+ (M2's spec: `channel` defaults to None) | P2 |
|---|---|---|
| sites | `inbox_of` (run), `_filed` (run), `_restored_concern` (run), `bus._withheld` (read, if peer `incident`) | placement loop (run), `_newest` (run), digest incidents (run, if peer `incident`; struck if "off boards by design" counts it out, S5 finding 8) |

| Case | H+ | P2 | Gap | By count (§(c) rule 3, band ≥ 2) |
|---|---|---|---|---|
| peer `incident`, digest site counted | 4 | 3 | 1 | tie |
| peer `incident`, digest site struck as by-design | 4 | 2 | **2** | **P2 wins outright** |
| no peer `incident` (`_withheld` unreachable, digest moot) | 3 | 2 | 1 | tie |
| any of the above, with `channel` keyword-only and no default (N1 forced: 4 mypy errors, run) | 3 / 3 / 2 | 3 / 2 / 2 | 0 / 1 / 0 | tie in every case |

**Corrected threshold arithmetic.** §(d) said "≥ 2 more H+-only sites" would reverse it. From
M2's H+ 2, P 3, P needs H+ − P ≥ 2, which is **net +3** H+-only sites (H+ 5 vs P 3). Net +2 is a
tie. The recount above adds net +2 with the digest site counted (a tie), and net +3 only once
the digest site is struck. The stated threshold is unchanged.

**Open in the rubric, not settled here.** Whether a pin test in a design's own spec takes a site
off the count. M2 counted `inbox_of` as silent while proposing a partition pin that would catch a
missing partition, so M2 read rule 1 as "only mypy uncounts". The counts above follow M2. If a
pin uncounts a site, H+'s `inbox_of`, `_filed` (with the kind pin) and `_restored_concern` (with
the end-to-end round trip) all drop, and P2's sites could be pinned the same way. That would need
re-measuring both columns, not just one.

### Tie-break (i), under both readings

H and K always carry rule 2's misread, so they are out under either reading.

- **Literal:** ruled out is any design that carries the unrepairable older-build misread when one
  site is forgotten.
  - H+ as M2 specified it, with only the round-trip pin, carries it at `_filed` (run, C).
  - P1 carries it too, at the widened `_filed` (M2 §(a); M2's run, relayed).
  - P2's peer writer is a new arm, forced by its new intent and written for peers. On S5's
    reading it carries the misread at no forgotten existing site.
  - **Verdict: P2.**
  - If the kind pin below is a stated acceptance criterion of H+, a forgotten `_filed` fails the
    gate instead of shipping. Whether that still counts as "carrying" is part of choosing the
    reading. If it does not count, H+ survives (i), and (ii) picks H+ over P2 for M2 §(d)'s
    reasons (relayed).
- **Narrow:** only a design that always carries it is ruled out.
  - H+, P1 and P2 all survive (i), and (ii) picks H+.
  - **Verdict: H+.**

| By-count case | literal, M2's pin only | literal, kind pin in H+'s spec | narrow |
|---|---|---|---|
| tie (peer `incident`, digest counted; or no peer `incident`; or required `channel`) | P2 | H+, if a pinned site is not "carried" | H+ |
| P2 outright (peer `incident`, digest struck, `channel` defaulted) | P2 | P2 | P2 |

### H+: the pin for `_filed`, and what it and a required construction path remove

The pin that turns the `_filed` choice from silent into failing asserts on the **encoded**
kind. That is what an older build dispatches on (`decode`'s `_KINDS.get(kind)`). It asserts
neither on the class nor on `isinstance(record, boardlog.Posted)`, which a correct `PeerPosted`
subclass of `Posted` would fail. In `tests/test_boardfacts.py`'s idiom:

```python
# Every kind a build before peer messages decodes. A peer message filed under any of
# these is read by such a build as whatever that kind means there.
PRE_PEER_KINDS = frozenset({
    "task-declared", "task-claimed", "task-released", "task-reclaimed",
    "task-version-handed", "task-completed", "task-closed", "task-amended",
    "task-writes-accrued", "concern-posted", "concern-edited", "concern-withdrawn",
    "concerns-delivered", "recording-stopped",
})


def test_a_concern_with_a_channel_is_filed_under_a_kind_no_older_build_knows() -> None:
    concern = Concern("c-1", LEAD, Z_LEAD, "subject", "body", posted_at=3.0, channel="x")
    _, facts = apply(started(), ConcernPosted(LEAD, concern))
    kinds = [json.loads(boardlog.encode(f.record))["kind"] for f in facts]
    assert kinds[0] == "peer-posted"
    assert kinds[0] not in PRE_PEER_KINDS
```

`m3_checks.py`'s `kind_pin` is this check. It fails under the widened `Posted` and passes under
`peer-posted` (run, C). The literal set equals this tree's `boardlog._KINDS` (run: compared at `e57b027` with
`PYTHONPATH` at this worktree). The test above was not itself run (it needs `Concern.channel`,
and its file would need `import json`). The set is meant to be frozen:
deriving it from `boardlog._KINDS` would let the pin pass once `peer-posted` had joined it.

**Do this pin and a required construction path for `_restored_concern` remove H+'s rule-2
exposure?**
- **The pin does, for `_filed`, for every change after it lands.** `_filed` is H+'s only rule-2
  site. S5 read it as the one place the kind is chosen, reached from `outcomes`. That was read,
  not run.
- **The pin cannot protect the change that lands it.** If the first change to write
  `peer-posted` ships without the pin, nothing catches it, so the pin has to be part of that
  change's acceptance. No peer record exists yet, so no record written before the pin is exposed.
- **A required construction path is about N1, and N1 is not rule 2.** Making `channel`
  keyword-only with no default makes N1 forced (4 `call-arg` errors, run, A). That takes one site
  off H+'s count and changes nothing about rule 2, because an older build never runs
  `_restored_concern` on a peer: it decodes `peer-posted` as `Unrecognised` and never places it
  (run, C).
- **N1's damage is not free either (read, not run).** While the copy is missing, B's `read_inbox`
  delivers the peer and `outcomes` writes its `Delivered`. After the copy is fixed, that message
  restores with its channel but delivered, so `read_peer_messages` never shows it. Messages
  posted after the fix are unaffected.
- **Together, the pin, M2's end-to-end round trip and a required `channel` leave H+ with no
  silent rule-2 site and no silent restore site.** H+'s remaining count is `inbox_of` (pinned by
  M2's partition test, still counted under M2's rubric) and the conditional `_withheld`.

### What M3 did not run

- B (`bus._withheld`): conditional on `send_to_peer` accepting `incident`, as instructed.
- No real H+ or P2 was built. The `peer-posted` subclass and the two `peers` collections are
  scratch shortcuts (above).
- No older branch build was run. This worktree stands in for "older", as in M2.
- The full pytest suite was not run on any variant, only the two named tests. mypy was not
  re-run on the `_restored_concern`-copies variant, or on the `store`-raises or
  generator-patched variants.
- P1's carrying the misread at `_filed` is M2's run, relayed. P2 carrying it at no forgotten site,
  and `_filed` being the only place the kind is chosen, are S5's readings, relayed.
- The digest-strike and peer-`incident` cases are presented, not decided.
