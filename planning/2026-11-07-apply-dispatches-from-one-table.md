# `_apply` dispatches from one table, and the table is its `match`

**Dated:** 2026-11-07 · **Status:** design only (B9 of
[2026-11-06-modules-are-named-for-what-they-require.md](2026-11-06-modules-are-named-for-what-they-require.md),
Round 3). Nothing is built. · **Asked by:** the lead, as board task r3-b9, under the build-rounds
premise (001-build-rounds.md). · **Evidence:** `git show HEAD:pptmstr/store.py` at **66af79e**
(4,724 lines; every `store.py:N` below is at that commit), an AST scan of `_apply` and of the
helpers each arm reaches (the script is in the appendix), three mypy probes of the dispatch forms,
and the B9 graph output in `2026-11-06-refactor-graph/monoliths.json`. Lines marked *run* were
executed; everything else is **read**.

r3-a4 is editing `store.py` while this is written. Everything here is against HEAD, not the
working tree.

## The answer in one paragraph

Keep the `match`. Make each arm a one-line call into a per-entity module under a `store/`
package, and move the helper clusters with their arms. A dict from Intent type to handler can
keep "one place to read". It cannot keep the property the match is there for: mypy names the
missing variant at a line (run, probe 1). A dict missing an entry type-checks clean and is caught
only at runtime (run, probe 2). A `match` whose arms are one-line calls is already a dispatch
table. Each narrowed arm type-checks against the handler it calls (run, probe 3), and
`assert_never` keeps exhaustiveness static. So the table form the plan proposes is built *as*
the match, not beside it. Moving the helpers alone (the other option the task names) is the first
stage of this, and is not enough on its own: it leaves 1,207 lines of arm bodies in the match. The
graph never counted those lines (see "Corrections"). Neither form conflicts with H+ → P. The dict
form would weaken a premise H+ → P's cost count rests on: that P's new intents are *forced* sites.

## (1) `_apply` at HEAD

### Its shape

`_apply` (store.py:447-1806) has three parts:

- **Prologue** (:460-479). `_drawable(intent)`, then copies of the twelve slices an arm may write:
  `nodes`, `concerns`, `tasks`, `unattributed`, `restores`, `decisions`, `commands`, `channels`,
  `channel_since`, `questions_aged`, `held_dismissed`, `questions_dismissed`. Then `effects` and
  `reorder`, and the `_RUN_REPORTS` guard, which drops a run's report about a recalled record.
- **The match** (:481-1742). 55 cases over 57 patterns. The 57 `Intent` members are each matched
  exactly once (run: no member is unmatched and no pattern is extra). No case has a guard. The
  default is `assert_never` at :1742.
- **Epilogue** (:1754-1806), run for every intent that falls out of the match. It does six things:
  - stamps `state_since` (:1754-1759);
  - clears `recalled` (`_no_longer_recalled`);
  - re-describes the peer rows (`_redescribed`/`_peer_rows`);
  - re-orders the tree (`_preorder`, if `reorder` is set);
  - rebuilds the `Snapshot`, with `needs_you`, `any_active` and `command_since` derived;
  - runs `_peer_waking`, then the chain `_owing_decisions → _owing_commands → _owing_wake_chains →
    _owing_peer_mail → _back_on_fleet`.

**An arm leaves in one of two ways, and the difference matters.** 30 arms have a
`return snap, ...` (run, scan). That return skips the epilogue entirely: there is no `seq` bump,
no waking and no re-derivation, and the caller gets back *the same snapshot object*. Every other
path falls through and runs the epilogue, even with nothing changed. The four-way no-op case
`SessionSettled | WakeChainReleased | PeerWakeSent | DecisionWakeSent` (:1717) changes nothing in
the match and exists *for* the epilogue: `_peer_waking` and `_moved` act on exactly those intents.
**An extracted arm therefore has to say which of the two exits it took.** Comparing the result to
`snap` by identity cannot tell "skip the epilogue" from "run it with nothing changed".

### Every arm (run, AST scan)

Lines are each case's span. "Writes" lists the locals the arm assigns or subscripts. "Reaches" is
the transitive closure of the module-level helpers it calls.

| Entity | Arm (store.py) | Lines | Writes | Reaches |
|---|---|---|---|---|
| **nodes** (15 arms, 256 lines) | `AgentSpawned` :482 | 38 | nodes, reorder | none |
| | `StateChanged` :521 | 42 | nodes | none |
| | `SubagentProgress` :564 | 24 | nodes | none |
| | `SubagentDelivered` :589 | 13 | nodes | none |
| | `TopicChanged` :603 | 5 | nodes | none |
| | `UsageAccrued` :609 | 5 | nodes | none |
| | `ContextPolled` :615 | 13 | nodes | none |
| | `CompactionObserved` :629 | 11 | nodes | none |
| | `AgentFinished` :770 | 10 | nodes | none |
| | `FailureAcknowledged` :781 | 5 | nodes | none |
| | `AgentResumed` :787 | 21 | nodes | none |
| | `AgentRemoved` :809 | 6 | nodes, reorder | `_subtree` |
| | `SessionRecalled` :1616 | 33 | nodes, reorder | none |
| | `SessionDetached` :1650 | 13 | nodes, effects | none |
| | `FailedSessionClosed` :1664 | 17 | nodes | none |
| **approvals** (2 arms, 127 lines) | `ApprovalRequested` :641 | 67 | nodes, reorder | `_peer_row`, `_summarised`, `_withdrawal_row` |
| | `ApprovalResolved` :709 | 60 | nodes, **tasks, unattributed** | `_claimed_tasks` |
| **mail** (4 arms, 70 lines) | `ConcernPosted` :816 | 20 | concerns, effects | `_concern_wake`, `_peer_fields_hold`, `_channel_as_linked` |
| | `InboxRead` :854 | 18 | concerns, effects | none |
| | `ConcernEdited` :970 | 4 | concerns | none |
| | `ConcernWithdrawn` :975 | 28 | concerns, effects | `_message_withdrawal_refusal`, `_withdraw_settled` |
| **peers** (9 arms, 142 lines) | `PeerSent` :873 | 47 | concerns, effects | none |
| | `PeerInboxRead` :921 | 48 | concerns, effects | `_read_covers` |
| | `ChannelLinked` :1682 | 17 | channels, channel_since, **nodes** | `_peer_rows` → `_peer_row`, `_summarised`, `_withdrawal_row` |
| | `ChannelClosed` :1700 | 8 | channels, **nodes** | the same |
| | `ChannelsRestored` :1709 | 7 | channels, channel_since, **nodes** | the same |
| | `SessionSettled \| WakeChainReleased \| PeerWakeSent \| DecisionWakeSent` :1717 | 4 | none | none (the epilogue acts on them) |
| | `PeerQuestionAged` :1722 | 4 | questions_aged | none |
| | `HeldMailDismissed` :1727 | 3 | held_dismissed | none |
| | `PeerQuestionDismissed` :1731 | 4 | questions_dismissed | none |
| **tasks** (10 arms, 380 lines) | `BoardRead` :837 | 16 | effects | none (`board.board_tasks`/`board_concerns`) |
| | `TaskAmended` :1004 | 76 | tasks, effects | `_amender_refusal`, `_settled` |
| | `TaskVersionHanded` :1081 | 13 | tasks | none |
| | `TaskDeclared` :1095 | 73 | tasks, effects | `_declaration_refusal`, `_already_behind`, `_as_asked`, `_auto_depends`, `_cycle`, `_would_cycle`, `_settled` |
| | `TaskClaimRequested` :1169 | 25 | tasks, effects | `_pick_claim`, `_claim_refusal` |
| | `TaskCompleted` :1195 | 34 | tasks, effects | `_ownership_refusal`, `_settled` |
| | `TaskReleased` :1230 | 35 | tasks, effects | `_may_reclaim`, `_may_take_claim`, `_root_of_its_session`, `_ownership_refusal`, `_settled` |
| | `TaskClosed` :1266 | 38 | tasks, **concerns** | none |
| | `TaskWithdrawn` :1305 | 49 | tasks, **concerns**, effects | `_withdrawer_refusal`, `_has_stopped`, `_may_take_claim`, `_root_of_its_session`, `_settled` |
| | `DigestRead` :1355 | 21 | **nodes**, effects | none (`digest.digest`) |
| **restore** (1 arm, 5 lines) | `BoardRestored` :1377 | 5 | **tasks, concerns, decisions, restores** | `_restored` and 16 more: `_restored_task`/`_concern`/`_decision`, the three `_timeless_*`, `_as_declared`, `_auto_depends`, `_cycle`, `_would_cycle`, `_channel_as_linked`, `_peer_fields_hold`, `_charter`, `_stated`, `_said_id`, `_damage_text` (786 lines) |
| **decisions** (5 arms, 96 lines) | `DecisionRaised` :1383 | 31 | decisions, effects | `_raise_refusal`, `_link_peer`, `_filed`, `_decision_settled`, `_asker_fate`, `blocked_tasks` |
| | `DecisionAnswered` :1415 | 10 | decisions, effects | `_ended_by_operator`, `_decided` → `_woken`, `_filed` |
| | `DecisionDismissed` :1426 | 8 | decisions, effects | the same |
| | `DecisionWithdrawn` :1435 | 11 | decisions, effects | `_withdrawal_refusal`, `_filed`, `_decision_settled` |
| | `DecisionsRead` :1447 | 36 | decisions, effects | `_filed` |
| **commands** (8 arms, 124 lines) | `CommandRequested` :1484 | 19 | commands, effects | `_request_refusal`, `_with_command`, `_command_settled`, `_asker_fate` |
| | `CommandApproved` :1504 | 6 | commands, effects | `_approved`, `_with_command` |
| | `CommandRefused` :1511 | 16 | commands, effects | `_closed`, `_with_command`, `_woken` |
| | `CommandWithdrawn` :1528 | 11 | commands, effects | `_command_withdrawal_refusal`, `_closed`, `_with_command`, `_command_settled` |
| | `CommandStopAsked` :1540 | 17 | commands, effects | `_with_command` |
| | `CommandEnded` :1558 | 30 | commands, effects | `_closed`, `_with_command`, `_woken` |
| | `CommandsRead` :1589 | 20 | commands, effects | `_with_command` |
| | `CommandsRestored` :1610 | 5 | commands | `_with_restored` |

Everything else in the module is reached from the epilogue. Its closure is 1,205 lines. It covers
the whole of the graph's "peer-mail waking" community (`_peer_waking`, `_moved`, `_to_tell`,
`_heard`, `_unheard`, `_still_owed`, `_wakeable`, `_owing_peer_mail`) and the obligations-text
community (`_owing_decisions`, `_owing_commands`, `_decision_summary`, `_command_summary`,
`_blocks_text`, `_asker_fate`, the `_*_TEXT` tables). Every top-level definition is reached from
`_apply` (run: none is unreached).

Helpers that more than one entity reaches (run) decide where the module boundaries can go:

| Helper | Reached from |
|---|---|
| `_settled` | the five task arms that answer |
| `_auto_depends`, `_cycle`, `_would_cycle` | `TaskDeclared` and the restore |
| `_peer_fields_hold`, `_channel_as_linked` | `ConcernPosted` and the restore |
| `_peer_row`, `_summarised`, `_withdrawal_row` | `ApprovalRequested`, the three channel arms, the epilogue |
| `_woken` | `_decided` (decisions) and `CommandRefused`/`CommandEnded` |
| `_asker_fate` | `DecisionRaised`, `CommandRequested`, the obligations text |
| `_may_take_claim`, `_root_of_its_session` | `TaskReleased`, `TaskWithdrawn` |

### Corrections to what was relayed

- **The graph's line counts are helper lines, not arm lines.** In `monoliths.json` every
  `_apply[case X]` node counts 1 line. The "restore 693, peer-mail waking 628, ..." figures
  therefore sum only the helpers in each community. **24 of the 55 arms are singletons** with no
  helper edge: all the node arms except `AgentRemoved`, plus `PeerSent`, `InboxRead`,
  `ConcernEdited`, `TaskClosed`, `TaskVersionHanded`, `DigestRead`, `BoardRead` and the small
  peer arms. Q 0.815 is a seam over the helpers. The graph can say nothing about where the 1,207
  lines of arm bodies go. That is the plan's own warning ("do not design a package from community
  membership"), and it applies here concretely. The entity grouping above is by what each arm
  writes, not by community.
- **The "peer-mail waking 628" community is not an arm cluster.** No arm calls it. It is the
  epilogue's `_peer_waking`.
- **The docstring's "single audit point for every mutation" (store.py:452-453) is not literally
  true.** The match is the only dispatch on the intent *into the twelve slices*. Three things
  write outside it:
  - `Snapshot.peer_wakes` is written **only** by the epilogue's `_peer_waking`
    (store.py:3098). No arm touches it.
  - `nodes` is written again by `_no_longer_recalled`, `_peer_rows` and `_back_on_fleet`.
  - `_peer_waking` (:2959) and `_moved` (:3329) each `match intent` again, with an open
    `case _` (:3060, :3364).

  All of these are reachable only from `_apply`. The audit point is therefore **`_apply`**: the
  match plus the epilogue. It is not the match alone. B9 should make the docstring say so (see
  Pins, P5).
- **A stale citation:** `tests/test_board_pane.py:3289` cites ``store._restore``, which does not
  exist at HEAD (run: it is the only one of 99 `store._name` citations that does not resolve). It
  presumably means `_restored`. Not fixed here.

## (2) What the single match buys, and the three forms

### What it buys

Four properties. The first three are what the docstring and STYLE §1 claim. The fourth is what
the epilogue's comments argue (:1744-1753).

1. **Static exhaustiveness at a line.** If a variant is missing, `assert_never` fails under mypy
   and names that variant (run, probe 1: `Argument 1 to "assert_never" has incompatible type "C"`).
   This is the property STYLE §1 calls the reason for sum types over base classes.
2. **Arm-to-type binding.** Inside `case X():` the intent is narrowed to `X`, so whatever the arm
   reads or passes on is checked against `X`.
3. **One door and one place to read.** `Store.apply_all` (:320) and `boardgraph`'s dry run (which
   imports `_apply` directly) are the only callers. Every intent enters through `_drawable` and
   the run-report guard. A reader finds every intent's effect from one function.
4. **Shared work no arm can forget.** The `state_since` stamp, the recall clearing, the peer-row
   re-description, waking and the derived fields run after *every* arm. As the epilogue's comment
   puts it, "an arm that has not been written yet cannot forget it". This is a property of having
   one function with a common tail. It does not depend on whether the dispatch is a `match` or a
   dict.

Per-aggregate reducers (one reducer per slice) lose 3 and 4. **Eight arms write more than one
entity's slice** (run, the bold cells above): `ApprovalResolved`, `TaskClosed`, `TaskWithdrawn`,
`DigestRead`, the three channel arms and `BoardRestored`. The epilogue writes `nodes` and
`peer_wakes` for every intent. Per-aggregate reducers stay refuted, as the plan said.

### The forms

**T: a dict table.** `_ARMS: Mapping[type, Callable[..., Step]]`, completeness asserted at import
and by a test, and `_apply` calls `_ARMS[type(intent)](snap, intent, now)`.

- Property 1 becomes a runtime check (run, probe 2). A plain dict missing a variant, or holding a
  mismatched pair, type-checks clean.
- Property 2 survives only behind a registration helper,
  `arm(t: type[J], f: Callable[[Snapshot, J, float], Step])` with `J` bound to `Intent`. mypy then
  rejects a mismatched pair (run, probe 2: `incompatible type "Callable[[A], int]"; expected
  "Callable[[B], int]"`).
- The call at the dispatch is still `Any`-typed. mypy cannot connect `type(intent)` to the
  value's parameter.
- It works only because no intent is subclassed (run: none is). A subclass would miss
  `type(intent)` and hit the default.
- **It buys** introspection: a test can list the handlers. `typing.get_args(Intent)` plus an AST
  read of the match buys the same (pin P1).

**M: keep the match as it is and move only the helper clusters** into per-entity modules.

- It keeps 1-4 untouched.
- It moves roughly the 2,900 helper lines (everything outside :447-1806 and the 446-line header).
- The match stays 1,262 lines. The node arms, the largest entity by arm lines, have no helpers,
  so M does not touch them at all.
- "One place to read" is no better than today: the place is 1,262 lines.

**M+: keep the match, make every arm a one-line call into its entity's module, and move the
helpers with their arms.**

- It keeps 1-4. Property 1 is unchanged. Property 2 holds because the narrowed `intent` is passed
  to a handler typed for exactly that variant (run, probe 3: passing the `A`-narrowed intent to
  the `B` handler is a mypy error).
- The match becomes ~115 lines, which is a better "one place to read" than today's 1,262.
- What it costs: one hop to read an arm's body, and a return type for arms (below).

**Recommendation: M+, built as M first.** M+ is the only form that keeps static exhaustiveness and
also makes the match readable as the table it is. T trades a mypy error for a test failure, and
gets back nothing that M+ lacks. M on its own leaves the bulk where it is. Stage 1 of M+ *is* M,
so if Stage 2 is declined, Stage 1 is still a coherent stopping point.

### The shape M+ needs

An extracted arm cannot rebind `_apply`'s locals, so it returns them. That needs one record and
one two-case union:

```python
@dataclass(frozen=True, slots=True)
class Draft:
    """
    The twelve slices an arm may write, and nothing it may not: seq, order,
    needs_you, any_active, command_since and peer_wakes are the epilogue's.
    """
    nodes: Mapping[NodeId, AgentRecord]
    concerns: Mapping[ConcernId, Concern]
    tasks: Mapping[TaskId, Task]
    unattributed_writes: ...
    restores: ...
    decisions: ...
    commands: ...
    channels: ...
    channel_since: ...
    questions_aged: ...
    held_dismissed: ...
    questions_dismissed: ...

    @staticmethod
    def of(snap: Snapshot) -> Draft: ...


@dataclass(frozen=True, slots=True)
class Unchanged:
    """
    The arm's `return snap, effects`: the caller gets back the same snapshot and
    the epilogue does not run.
    """
    effects: tuple[Effect, ...] = ()


@dataclass(frozen=True, slots=True)
class Stepped:
    draft: Draft
    effects: tuple[Effect, ...] = ()
    reorder: bool = False


Step = Unchanged | Stepped
```

The match becomes `case TaskDeclared(): step = tasks.declared(snap, intent, now)`. The four-way
no-op becomes `step = Stepped(Draft.of(snap))`. After the match, a second
`match step: ... case _: assert_never(step)` sends `Unchanged` straight back and sends `Stepped`
into today's epilogue.

**Why a `Draft` and not a `Snapshot` built with `dataclasses.replace`.** Using the snapshot would
need no new record. The cost is that an arm could then hand back a `peer_wakes`, a `seq` or a
`needs_you`, and the epilogue would have to ignore it silently. That is a field nothing honours, a
smell STYLE §3 has already paid for. A `Draft` without those fields makes "only the epilogue
writes them" a type fact, not a convention. `ConcernWithdrawn`'s `after = dataclasses.replace(snap,
concerns=...)` (:996) stays inside its arm, as it is today.

**The layout.** `pptmstr/store.py` becomes `pptmstr/store/__init__.py`. A package, not flat
`store_*` siblings, because a new prefix family is exactly what the operator flagged.

- `__init__` keeps `Store`, `Observer`, `_clock`, `_VERBATIM`, `_drawable`, `_RUN_REPORTS`,
  `_apply` (prologue, match and epilogue), `Draft` and `Step`. That is the plan's T6: a reader
  finds the step from the package's `__init__`.
- The entity modules are all pure, so their names are domain names:

| Module | Arms | Helpers |
|---|---|---|
| `store/nodes.py` | the 15 node arms | `_subtree`, `_preorder`, `_no_longer_recalled`, `_back_on_fleet` |
| `store/approvals.py` | `ApprovalRequested`, `ApprovalResolved` | `_peer_row`, `_peer_rows`, `_withdrawal_row`, `_summarised`, `_redescribed`, `_claimed_tasks` |
| `store/tasks.py` | the 10 task arms | the declaration/cycle, lifecycle and claim clusters, and `_settled` |
| `store/mail.py` | `ConcernPosted`, `InboxRead`, `ConcernEdited`, `ConcernWithdrawn` | `_concern_wake`, `_message_withdrawal_refusal`, `_withdraw_settled` |
| `store/peers.py` | `PeerSent`, `PeerInboxRead`, the channel arms, the four no-op arms, the three dismissal/age arms | `_peer_fields_hold`, `_channel_as_linked`, `_read_covers`, `_link_peer` |
| `store/waking.py` | none | `_peer_waking`, `_moved`, `_to_tell`, `_heard`, `_unheard`, `_still_owed`, `_wakeable`, `_owing_wake_chains`, `_owing_peer_mail`, `_peer_pair` |
| `store/decisions.py` | the 5 decision arms | `_raise_refusal`, `_filed`, `_decided`, `_decision_settled`, `_ended_by_operator`, `_withdrawal_refusal` |
| `store/commands.py` | the 8 command arms | `_request_refusal`, `_with_command`, `_approved`, `_closed`, `_command_settled`, `_command_withdrawal_refusal`, `_with_restored`, `_command_since` |
| `store/owing.py` | none | `_needs_you`, `_owing_decisions`, `_owing_commands`, the summaries, `_asker_fate`, `blocked_tasks`, the `_*_TEXT` tables, `_woken` |
| `store/restore.py` | `BoardRestored` | `_restored` and its 16 |

The imports point one way only: restore → tasks, peers; decisions/commands → owing; approvals →
peers. Nothing imports `__init__` except the outside world.

## (3) Against H+ → P

**H+ is built at HEAD.** `Concern` carries `channel`/`link`/`re`/`quiet`, `PeerSent` exists, and
`peer-posted` is a kind in `boardlog`, `boardfacts`, `intents` and `model`. **P is not scheduled
anywhere.** The only mentions are 2026-10-17:219 and m2-judgement.md:257 ("H+ → P: a store
refactor only"). So the collision to plan for is the shape, not a concurrent branch. P (P2)
touches the store at these points (m2-judgement §(e)):

- two new intents, each with an arm in `_apply`;
- a `Snapshot.peers` map;
- `_restored` placement and its `_timeless` twin;
- the peer branches of `ConcernPosted`/`ConcernWithdrawn`/`PeerSent`/`PeerInboxRead`;
- `_peer_waking`'s `PeerSent` case, which reads `world.concerns`.

Against M+:

- **New intents: no conflict, and that is the argument for M+ over T.** m2-judgement §(b) counts
  P's new arms in `_apply`, `outcomes` and `fleetsync` as *forced*, meaning mypy points at them.
  Under M+ they stay forced. Under T, `_apply`'s would become a test failure, not a type error.
  That quietly changes a premise of the recorded H+/P count. **This is the one real conflict, and
  it belongs to T, not to M+.**
- **`Snapshot.peers`: additive.** It becomes a thirteenth `Draft` field, plus `Draft.of` and the
  epilogue's `Snapshot(...)` call. mypy forces all three, as it forces the local and the
  constructor today.
- **Where the peer arms live: one placement choice could conflict.** Under H+ a peer message is a
  `Concern`, and four `mail` arms branch on `is_peer`. If B9 sorted those branches into `peers.py`
  by splitting the arms, P would find half-split arms. **So B9 moves `ConcernPosted`,
  `ConcernEdited` and `ConcernWithdrawn` whole into `mail.py`, `is_peer` branches included.**
  `PeerSent`/`PeerInboxRead`, which are peer-only, go to `peers.py`. P then splits the mail arms,
  which it would have done anyway. That keeps B9 a pure move, with no behaviour or shape decided
  ahead of P.
- **Textual collision if both run at once.** Stage 2's `mail`/`peers`/`restore` moves and P edit
  the same bodies. They must not be on the board together. Either one goes first. Neither changes
  the format.

**Other collisions** (read):

- **r3-a4 (live).** A4 merges the allowance tails of `_raise_refusal` and `_request_refusal`
  (decisions and commands). B9 starts after A4 lands. The merged function goes to `owing.py` if
  both reach it, or stays where A4 puts it if that is already a neutral place.
- **B6** (inter-lead channels get one owning package). B9 puts the *store's* peer arms and waking
  under `store/`. If B6 chooses domain-first packaging (`peer/store.py`), the two layouts
  contradict each other. Either is compatible with the audit point, as long as only `_apply`
  imports the arm modules (P2 below). **The lead should decide which wins before either is
  built.**
- **B1/B5 and `STORE_DISK_EXCEPTIONS`.** `approval` and `boardlog` are named exceptions to the
  store's no-disk rule (`tests/test_layers.py:488`). After the move, whichever submodules import
  them carry the exception. The rule has to cover the package (patch site T1).

## (4) Pins first, the move order, the patch sites

### Pins, written against HEAD's `store.py` before anything moves

Each pin is mutation-tested against the current line, as STYLE §2 asks.

- **P1. Every Intent has its own case** (`tests/test_store_dispatch.py`). Read `_apply`'s match by
  AST, collect its class patterns, and assert they equal `get_args(Intent)` with no duplicates and
  a final `assert_never`. mypy already holds this; the test holds it when `assert_never` is
  replaced by `pass` or a `case _:` grows a body. *Mutation:* delete an arm and turn
  `assert_never` into `pass`. mypy is then silent, and P1 must fail.
- **P2. `_apply` is the only caller of an arm.** By AST over `pptmstr/`, no module outside
  `pptmstr.store` imports from `pptmstr.store.<entity>`. Within the package, the entity modules'
  arm functions are called only from `pptmstr/store/__init__.py`. Before the move this passes
  vacuously, so mutation-test it after the first Stage 2 commit: import an arm from `bus.py`, and
  it must fail.
- **P3. The two exits.** Parametrised over one representative intent per early-return path
  (30 arms, run, scan), assert the no-op returns the identical snapshot
  (`after is before`, `seq` unchanged, the same effects). Separately, assert that each of the four
  no-op-for-the-match intents bumps `seq` and reaches `_peer_waking` (one test per intent, with a
  link whose waking it changes). This is the behaviour a `Draft`/`Step` translation most easily
  breaks, and no existing test is known to enumerate it (not checked; see "Not run").
  *Mutation:* make one early return fall through. Only P3 must catch it.
- **P4. `store.<name>` citations resolve.** This generalises
  `tests/test_approval.py:1812`/`:1817`. Every `` `store._name` `` or ``store._name`` in
  `pptmstr/`, `tests/` and `scripts/` resolves to a name in `pptmstr.store` or one of its
  submodules, written as `store.tasks._may_reclaim` once moved. At HEAD this finds the one stale
  `_restore` (run, by the same regex), which gets fixed in the pin's commit. Without P4, the 94
  citations in comments and docstrings go stale silently as helpers move.
- **P5. The docstring states the audit point as it is.** Reword `_apply`'s docstring: the audit
  point is `_apply`, meaning the match for the twelve slices, plus the epilogue phases named in
  order as the only other writers (`peer_wakes` only in `waking`). This is not a test. It is the
  claim P1 and P2 hold the code to.
- **P6. Purity.** Add each entity module to `test_layers.PURE` (none reads a clock), and widen the
  store rule to the package (T1 below).

### Move order (each commit is green on `make check`; no shims)

0. **A4 lands.**
1. **Pins P1, P3, P4** on `store.py` as it is, plus P4's one fix.
2. **`store.py` → `store/__init__.py`** (`git mv`), with no other change. **Every relative import
   gains a dot.** Inside a package `__init__`, `from . import drawable` means
   `pptmstr.store.drawable`, so `from .approval import ...` becomes `from ..approval import ...`,
   for all 12 relative imports (:34-216). In `test_layers`, `imports_of("pptmstr.store")` now
   resolves with `is_package=True`. Check that the store rule still sees `approval`/`boardlog`.
   P2 and P6 are added here.
3. **Stage 1 (M): helpers, coldest and fewest citations first.**
   - **restore:** 1 arm caller, 2 citations.
   - **owing**, then **waking:** 13 citations, including `app.py`'s `_still_owed`/`_wakeable`
     and `tests/test_peer_wakes.py:1562`.
   - **commands**, then **decisions** (after A4).
   - **approvals/peers.**
   - **tasks** last: 40+ citations, `scripts/verify_declaration_units.py:50` imports
     `_auto_depends`, and `board.py`/`bus.py`/`approval.py` cite its helpers.

   Each commit rewrites that cluster's citations, which P4 enforces.
4. **Stage 2 (to M+): add `Draft`/`Step`, then move arm bodies in the same order.** Translate
   mechanically: `nodes[...] = ` becomes a local dict built from `snap.nodes` and returned in the
   `Draft`; `return snap, x` becomes `return Unchanged(x)`. **nodes** and **tasks** go last: they
   hold the most arm lines and are the most churned.
5. **P5** with the last move, when the docstring can name the modules.

### Patch sites (run: grep at HEAD, 99 `store._name` references outside `store.py`)

- **Code imports of moved names** (6):
  - `scripts/verify_declaration_units.py:50` (`_auto_depends`);
  - `tests/test_needs_you.py:707` (`_FATE_TEXT`);
  - `tests/test_peer_tools.py:62` and `tests/test_peer_wakes.py:70` (`_concern_wake`);
  - `tests/test_peer_wakes.py:1562` (`_wakeable`);
  - `tests/test_store.py:6137` (`store_module._owing_decisions`).
- **Kept in `__init__`, no change:** `Store`, `Observer`, `_apply` (`boardgraph.py:41`, and 18
  test files), `_VERBATIM` (`tests/test_verbatim_drawn.py:64`, `tests/test_drawable.py:563`),
  `_RUN_REPORTS` (`tests/test_store.py:6273`).
- **Citations checked by a test:** `tests/test_approval.py:1812` (`_may_reclaim`) and `:1817`
  (`_approved`), together with the text in `approval.py` they read.
- **Citations in comments and docstrings** (~90). The heaviest are `model.py`, `intents.py`,
  `approval.py`, `bus.py`, `board.py` and `boardlog.py`. P4 makes the list exact for each commit.
- **`tests/test_layers.py`:**
  - (T1) the store rule over `pptmstr.store` and its submodules;
  - `STORE_DISK_EXCEPTIONS` moves to whichever submodules import `approval`/`boardlog`;
  - `PURE` gains the entity modules.
- **Planning text that cites `store.py:N`:** not patched. Planning records are dated, and
  file:line references are taken at a named commit.

## (5) What would refute this

- **mypy does not keep narrowing through the one-line arms** under the repo's `strict` settings at
  Python 3.11. Probe 3 ran `--strict --python-version 3.11` on a 2-variant toy, not on `store`.
  Stage 2's first commit would show it.
- **An arm reads a slice that an earlier part of `_apply` already modified,** so that building
  its `Draft` from `snap` differs from today's shared locals. By reading, the prologue writes
  nothing before the match, so every arm starts from `snap`'s values. Disproved per arm if P3 or
  the suite fails after its move.
- **A real reason the open `case _` in `_peer_waking`/`_moved` must see intents the match has
  already transformed differently.** By reading, both take the post-`_drawable` intent, as
  today. Moving them changes nothing.
- **B6 is decided domain-first.** Then the `peers`/`waking` placement moves, but the dispatch
  design stands.
- **Per-intent cost of the second `match step`.** It adds one isinstance per intent, which is
  negligible at the stated scale (store.py:9-14). Not measured.
- **If the operator values introspection** (a handler registry a plugin could extend) over static
  exhaustiveness, T wins. Nothing in the repo asks for that, and STYLE §1 asks for the opposite.

## Open, for the lead

- Whether `_peer_waking` and `_moved` should close their `case _` with `assert_never`, as B4 did
  for `channels.outcomes`. Their docstrings argue that most intents deliberately neither wake nor
  move, so the open default may be a decision rather than drift. **This design does not settle
  it.** It flags that the docstring's "single audit point" currently hides two open
  re-dispatches.
- B6 vs B9 on where the store's peer half lives.

## What was not run

- **Run:**
  - the AST scan of `_apply` (arms, spans, writes, returns, helper closure) at 66af79e;
  - the `Intent` coverage check;
  - the subclass check;
  - the citation-resolution check (99 references, one stale);
  - three mypy probes (`mypy 2.4.0 --strict --python-version 3.11`) on toy unions:
    - match with a missing arm: error at `assert_never`;
    - dict missing an entry: clean, caught only by a runtime `get_args` diff;
    - registration helper with a mismatched pair: error;
    - narrowed arm passed to the wrong handler: error.
- **Not run:**
  - no code was changed, and no part of M or M+ was built;
  - `make check` was not run (nothing to check);
  - the probes did not run on `store` itself;
  - P1-P6 are not written;
  - whether existing tests already enumerate the two exits (P3) was not checked;
  - the per-intent dispatch cost was not measured;
  - the DAG of the proposed submodules is read, not import-tested;
  - B6's and A4's final shapes are unknown, and A4's working-tree edits were not read.
- The scan and probe scripts were in `$TMPDIR` and are not kept. The scan's method is below so it
  can be re-run.

## Appendix: the scan

```python
# python scan.py <(git show 66af79e:pptmstr/store.py)
import ast, sys
tree = ast.parse(open(sys.argv[1]).read())
top = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
apply = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_apply")
match = next(n for n in apply.body if isinstance(n, ast.Match))
for case in match.cases:
    body = ast.Module(body=case.body, type_ignores=[])
    called = {c.func.id for c in ast.walk(body)
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in top}
    returns = [ast.unparse(r.value) for r in ast.walk(body) if isinstance(r, ast.Return)]
    print(case.pattern.lineno, case.body[-1].end_lineno, ast.unparse(case.pattern),
          sorted(called), returns)
```

The closure follows each helper's own references to top-level names. "Writes" is `Name`/`Subscript`
stores, and `pop`/`setdefault` calls, on the twelve prologue locals plus `effects`/`reorder`.

## Names moved by B5's last step

The text above cites these names in `approval.py`. B5's last step
(`planning/2026-11-07-board-and-gate-packages.md`, Order step 4) split what was
left of `approval.py` into `pptmstr/gate/` (`rows`, `reach`, `classify`, `park`)
and deleted it. A line number cited above is the old file's, when the text was
written.

| Cited as | Now |
|---|---|
| `approval.py` | deleted; its parts are `pptmstr/gate/rows.py`, `reach.py`, `classify.py` and `park.py`, beside the leaves |
