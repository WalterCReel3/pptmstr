# Inter-lead channels have one owning package, and every minted id has one owner

**Dated:** 2026-11-07 · **Status:** design only (B6 of
[2026-11-06-modules-are-named-for-what-they-require.md](2026-11-06-modules-are-named-for-what-they-require.md),
Round 3). Nothing is built. One choice is left to the lead: the layout B9 proposes for the store's
peer helpers contradicts this one (see "Against B9 and B7"). · **Asked by:** the lead, as board
task r3-b6, under the build-rounds premise (001-build-rounds.md). · **Evidence:** HEAD **66af79e**,
exported with `git archive` into `$TMPDIR/b6/head`, because r3-a4 is editing `store.py` in the
worktree. Every `file:line` below is at that commit. Four AST scans were run over the export
(described under "How the maps were made"), plus the channel test suites. Lines marked *run* were
executed; everything else is **read**.

## The answer in one paragraph

Channels are about 4,600 lines outside the ui (*run*), not 2,500: 1,168 in `channels.py` and about
3,450 in eight other modules. Another 657 sit in seven ui modules, for about 5,300 in all. Most of it can have one owner. The
exceptions are the members of the three closed unions (Intent, Effect, Obligation), `_apply`'s nine
channel arms, `Concern`'s peer fields, and the ui. The first two stay where they are for STYLE §1's
reason, which is one place to read the union. `Concern`'s peer fields are the ground the pending
H+ → P refactor rebuilds. The ui stays because of layer rule 3.

The package is `pptmstr/channels/`, with one module per requirement. `records` needs only `ids`,
so `model` can import it. `projections` needs a `Snapshot`. `reducer` needs model, intents and
effects, and only `store._apply` calls it. `text` is B7's peer renderers. `log` is today's
`channels.py` (disk and a thread).

Id minting is 33 production sites (*run*). The prefix reuse cannot cause a collision, because no
code reads a request id's prefix. All request ids are keys in one dict,
`Bridge._requests` (bridge.py:273), and their uniqueness rests on 48 random bits. No id is compared
across kinds (*run*). `ids.py` owns the id aliases, the mints and the shapes ids are read back by.
It imports nothing from pptmstr. A layer rule makes `uuid` and `secrets` importable only there.

## How the maps were made (run)

Every scan read the HEAD export with `.venv/bin/python` and `ast`, and imported nothing from
pptmstr. The scripts are in `$TMPDIR/b6/` and are not kept, so their rules are given here so the
scans can be rerun.

- **Channel definitions** (`scan_channels.py`). This takes every module-level or class-level def and
  assignment whose own name matches
  `channel|peer|link|charter|wake_?chain|whose_?move|held_?mail|owed_answer|awaiting_answer|cited_question|is_answered|Thread*|LinkThread`.
  It drops the homonyms, which are hyperlinks (`theme.link_inks`, `ui/widgets.link`,
  `ui/typeset/views.follow_links`, `ui/typeset/gate._linked`, `ui/tool_block.link_inks`), git
  linked worktrees (`ui/launcher.LinkedWorktree`, `_RUNS_BY_LINK`), `boardsync.Linked` (a
  migration link), `requests.Thread` and `figures`' colour `channel`. It also takes each `case` of
  `store._apply` whose class pattern names one of the twelve channel intents.
- **The closure** (`closure.py`). These are functions whose every in-module caller is already a
  channel definition, with `_apply`'s channel arms counted as one caller. It is computed to a fixed
  point. Method names are matched bare, so the model results for `is_open` and `answer` are false
  positives and are dropped.
- **The coupling** (`deps.py`). For each module's channel set, it lists the non-channel functions that
  call into it and the non-channel functions it calls. This is what decides what can move without a
  cycle.
- **Ids** (`scan_ids.py`). This takes every call to `uuid.uuid4`, `secrets.token_hex`, `os.urandom`, or
  `next()` on a module-level `itertools.count`, together with the literal head of any f-string around
  the call and the hex width it slices.
- **Cross-kind lookups** (`cross_kind.py`). This takes every function in which one expression is
  used as a key into two different `Snapshot` id maps (`tasks`, `concerns`, `decisions`, `commands`,
  `channels`, `nodes`, `peer_wakes`, `restores`).
- **Test binding sites** (`patch_sites.py`). This counts `from pptmstr.M import N`, `M.N` reads,
  `monkeypatch.setattr(M, "N")` and dotted-string patches over `tests/`, for every channel name.

Sizes are def spans, from the `def`/`class` line to `end_lineno`, docstrings included and leading
comments excluded. They are lower bounds. A channel line inside a non-channel function (for
example the `is_peer` branch of `ConcernPosted`, or the peer clause of `bus._decisions_text`) is
not counted.

## (1) Every channel definition at 66af79e (run)

### Totals

| Module | Lines | What |
|---|---|---|
| `channels.py` | 1,168 (whole file) | the link log: facts, codec, fold, `ChannelLog` seam, `FileChannels`, `restore_all`, `outcomes`, `ChannelWriter` |
| `model.py` | 1,175 | records about 450, `Snapshot` methods about 600, obligations 122 |
| `store.py` | about 800 helpers + 142 in arms | reducer helpers, and the nine `_apply` arms |
| `bus.py` | 531 | the three peer tools and the peer text |
| `app.py` | 345 | link/close/restore wiring, log reports, peer wakes, the question timers |
| `intents.py` | 199 named + about 40 | ten channel intents, plus `SessionSettled`, `DecisionWakeSent` |
| `effects.py` | 188 | peer effects, `PeerIdentity` and its text |
| `boardlog.py` | 59 | `PeerPosted`, `PeerRetracted` (board-log record kinds) |
| `approval.py` | 21 | the peer tool names and `peer_channel` |
| ui (7 modules) | 657 | `inbox` 180, `session_menu` 120, `decision_view` 86, `link_form` 82, `rail` 74, `detail` 73, `focus` 42 |

### Records (model.py)

The column "deps in model" is what the scan found each record referring to. It decides whether the
record can sit below `model`.

| file:line | lines | name | deps in model |
|---|---|---|---|
| model.py:848 | 28 | `WakeChainHeld` (Obligation) | NodeId, ObligationKind |
| model.py:879 | 53 | `PeerQuestionUnanswered` (Obligation) | ChannelId, ConcernId, NodeId, ObligationKind |
| model.py:935 | 41 | `PeerMailHeld` (Obligation) | the same |
| model.py:1793-1844 | about 50 | `Concern.channel/needs_answer/re/quiet/link/withdrawal_told`, `__post_init__`, `is_peer` | the `Concern` record |
| model.py:2348 | 58 (+4 consts) | `Charter` | none |
| model.py:2425-2480 | about 50 | `MAX_CHANNEL_CHARS`, `_CHANNEL_NAME`, `_channel_refusal`, `is_channel_name`, `channel_refusal` | none |
| model.py:2485, :2489 | 2 | `ChannelId`, `LINK_MEMBERS` | none |
| model.py:2492 | 11 | `LinkRefusal` | none |
| model.py:2506 | 35 | `Channel` | ChannelId |
| model.py:2543-2560 | 18 | `EveryChannel`, `EVERY_CHANNEL`, `ChannelScope` | none |
| model.py:2584-2588 | 76 | `PEER_WAKE_CHAIN`, `PeerWakes` | DecisionId |
| model.py:2665, :2683 | about 32 | `MoveOwner`, `WhoseMove` | none |
| model.py:2713 | 24 | `PeerIndex` | **PendingApproval** |
| model.py:2739-2814 | about 61 | `ThreadMark`, `ThreadMessage`, `ThreadDecision`, `ThreadParked`, `ThreadWakeHeld`, `ThreadRow` | **Concern, Decision, PendingApproval** (Mark, WakeHeld: none) |
| model.py:2818, :2838 | 27 | `ThreadSide`, `LinkThread` | Channel, ThreadRow |
| model.py:2852 | 1 | `WITHDRAW_PEER_TOOL` | none |
| model.py:2855, :2896 | 67 | `PeerRefusal`, `PeerWithdrawRefusal` | none |
| model.py:2950, :2974 | 31 | `why_held`, `_thread_mark` | **Snapshot, Concern** |
| model.py:2985 | 8 | `peer_addressed` | NodeId |

### Projections (model.py, `Snapshot` methods)

These are 26 methods, about 600 lines:

- `peer_inbox_of` :4133
- `peer_inbox_named` :4150
- `peer_deliverable` :4163
- `peer_unread` :4187
- `peer_untold` :4198
- `owed_answers` :4206
- `peer_owing` :4220
- `withdrawn_untold` :4232
- `cited_question` :4268
- `is_answered` :4292
- `_answered` :4299
- `awaiting_answer` :4311
- `questions_unread` :4345
- `peer_held` :4377
- `wakeable` :4398
- `parked_link` :4413
- `peer_index` :4441 (88)
- `whose_move` :4530
- `parked_on` :4582
- `thread` :4595 (68)
- `linked_after` :4664
- `peer_send_refusal` :4680
- `peer_route` :4702
- `channels_of` :4724
- `live_channel` :4748
- `link_refusal` :4752

`channel_decision_readable` :4860 is a channel projection that **cannot move**: `decisions_readable_by`
(model.py:4833), which is not a channel projection, calls it, so moving it above `model` would make a
cycle (`deps.py`). The other inward callers either move too (`questions_unread`) or stay on the
record (`inbox_of` reads `Concern.is_peer`).

There are six `Snapshot` fields: `channels`, `channel_since`, `peer_wakes`, `questions_aged`,
`held_dismissed` and `questions_dismissed` (model.py:4083-4109).

### Intents (intents.py)

| file:line | lines | intent |
|---|---|---|
| :338 | 41 | `PeerSent` |
| :382 | 26 | `PeerInboxRead` |
| :1271 | 32 | `ChannelLinked` |
| :1306 | 11 | `ChannelClosed` |
| :1320 | 12 | `ChannelsRestored` |
| :1335 | about 22 | `SessionSettled` (peer wake at resume) |
| :1359 | 11 | `WakeChainReleased` |
| :1373 | 23 | `PeerWakeSent` |
| :1399 | about 19 | `DecisionWakeSent` (channel decisions) |
| :1419 | 17 | `PeerQuestionAged` |
| :1439 | 11 | `HeldMailDismissed` |
| :1453 | 15 | `PeerQuestionDismissed` |

### Reducer (store.py)

The arms are in `_apply`, 142 lines in total:

| Arm | Line | Lines |
|---|---|---|
| `PeerSent` | :873 | 47 |
| `PeerInboxRead` | :921 | 48 |
| `ChannelLinked` | :1682 | 17 |
| `ChannelClosed` | :1700 | 8 |
| `ChannelsRestored` | :1709 | 7 |
| `SessionSettled \| WakeChainReleased \| PeerWakeSent \| DecisionWakeSent` | :1717 | 4 (a no-op; `_peer_waking` does the work) |
| `PeerQuestionAged` | :1722 | 4 |
| `HeldMailDismissed` | :1727 | 3 |
| `PeerQuestionDismissed` | :1731 | 4 |

The helpers are about 800 lines:

| Helper | Line | Lines | Note |
|---|---|---|---|
| `_peer_row` | :1807 | 42 | |
| `_withdrawal_row` | :1851 | 21 | closure |
| `_summarised` | :1874 | 9 | closure |
| `_peer_rows` | :1885 | 16 | |
| `_link_peer` | :2736 | 11 | |
| `_peer_waking` | :2868 | 231 | |
| `_to_tell` | :3101 | 30 | closure |
| `_heard` | :3133 | 31 | closure |
| `_unheard` | :3166 | 51 | closure |
| `_still_owed` | :3219 | 49 | closure |
| `_read_covers` | :3270 | 13 | channel-only by reading; the scan counts `_apply` as non-channel |
| `_wakeable` | :3285 | 7 | closure |
| `_moved` | :3294 | 72 | closure |
| `_owing_wake_chains` | :3368 | 27 | |
| `_owing_peer_mail` | :3397 | 143 | |
| `_peer_pair` | :3542 | 3 | |
| `_charter` | :4328 | 9 | |
| `_channel_as_linked` | :4572 | 14 | |
| `_peer_fields_hold` | :4588 | 16 | |

**The coupling is clean** (`deps.py`, *run*).
- The set calls exactly one store function outside itself, `_read_covers`, which is channel-only.
- It is called only from `_apply` and from three restore helpers: `_restored_concern` (calls
  `_peer_fields_hold`), `_restored_decision` (calls `_channel_as_linked`) and `_restored_task` (calls
  `_charter`).

### Effects (effects.py)

| file:line | lines | name |
|---|---|---|
| :104 | 26 | `PeerIdentity` (plain strings) |
| :132 | 23 | `peer_identity(snap, …)` |
| :160 | 1 | `PEER_TITLE_CHARS` |
| :193 | 31 | `peer_text` |
| :227 | 1 | `PEER_SHORT_TITLE_CHARS` |
| :230 | 14 | `peer_forms` |
| :247 | 25 | `PeerSettled` |
| :275 | 21 | `PeerWithdrawSettled` |
| :299 | 45 | `PeerInboxDelivered` |
| :614 | 1 | `WakeReason.PEER_MESSAGE` |

### Bus tools and text (bus.py)

The tools are three nested handlers in `build_server`:

| Tool | Line | Lines |
|---|---|---|
| `send_to_peer` | :4214 | 55 |
| `read_peer_messages` | :4295 | 29 |
| `withdraw_peer_message` | :4355 | 50 |

`PEER_TOOLS` is at :5674.

The text definitions:

| Definition | Line | Lines |
|---|---|---|
| `_CHARTER_UNKEPT` | :474 | |
| `_PeerMessage` | :3465 | 9 |
| `_stated_peer_message` | :3476 | 53 |
| `_peer_refusal_text` | :3531 | 52 |
| `_withdrawn_message_lines` | :3625 | 27 |
| `_PEER_LEGEND` | :3696 | |
| `_CHARTER_LEGEND` | :3704 | |
| `peer_said` | :3711 | |
| `_linked_now` | :3754 | |
| `_links_lines` | :3765 | 25 |
| `_peer_message_lines` | :3792 | 29 |
| `_peer_messages_text` | :3823 | 72 |
| `_peer_wake_said` | :5731 | |
| `_across_a_link` | :5790 | |
| `peer_wake_line` | :5845 | 65 |

The text depends on four non-channel bus helpers: `_edited_note`, `_length_refusal`, `_refusal_text`
and `_titled`. Non-channel bus code calls `peer_said` (4 sites), `_linked_now` and `peer_wake_line`
(`deps.py`).

### App wiring (app.py)

| file:line | lines | name | takes `AppState` |
|---|---|---|---|
| :1254 | 21 | `_say_peer_wakes_spent` | yes |
| :1300 | 19 | `_still_to_read(snap, wake)` | **no** |
| :1319 | about 40 | `_age_questions` | yes |
| :1421 | 40 | `_record_channels` (observer) | yes |
| :1463 | 25 | `_read_channel_reports` | yes |
| :1490 | 11 | `_read_outcome` | yes |
| :1503 | 11 | `_stopped` | **no** |
| :1516 | 33 | `_read_sweep` | yes |
| :1559 | 49 | `_sweep_notices` | **no** |
| :1610 | 18 | `_not_written` | **no** |
| :1630 | 7 | `_link_pressed` | yes |
| :1644 | 46 | `link_leads` | yes (mints the channel id, :1681) |
| :1692 | 32 | `close_channel` | yes |
| :1726 | 34 | `_restore_channels` | yes |
| :3925 | 3 | `_close_channel_pressed` | yes |
| :5213 | 13 | `_close_channel_log` | yes |

`AppState.channel_writer` is at :971. `app` is the **only** importer of `channels` (app.py:34,
*run*).

### Elsewhere

- `boardlog.PeerPosted` :432 (38) and `PeerRetracted` :494 (21). These are board-log kinds, filed in the
  sender's log (Decided 6).
- `boardfacts._filed`'s peer branch (:600-650).
- `fleetsync`'s closed-match arms (:280-317).
- approval.py:133-142 (tool names) and `peer_channel` :1009 (17).

## (2) Every id-minting site, and whether prefixes can collide (run)

There are 36 sites. Three of them are in `fake_driver` (a test double with counters by design), which leaves 33 production sites.

**Request ids: 23 sites, one namespace, always 12 hex characters.**

| prefix | sites |
|---|---|
| `v-` | bus `board_row` :3942, `withdrawn_row` :3958, `board_ids` :3990 |
| `q-` | bus `board_world` :4014, `raise_decision` :5457 |
| `x-` | bus `withdraw_peer_message` :4373, bus `release_task` :5192, **commandbus** `withdraw_command` :995 |
| `k-` | bus `claim_task` :4422, **commandbus** `request_command` :897 |
| `w-` | bus `withdraw_task` :5260, `withdraw_decision` :5585 |
| one each | `r-` read_inbox :4137, `p-` send_to_peer :4225, `m-` read_peer_messages :4308, `b-` read_board :4504, `o-` read_task :4562, `g-` read_digest :4638, `d-` declare_task :4894, `a-` amend_task :5025, `f-` complete_task :5117, `u-` read_decisions :5553, `j-` commandbus read_commands :966 |

The relayed claim is half right. Only `x-` and `k-` are reused **across** bus and commandbus. `v-`
and `q-` are reused only inside bus, and `w-` is reused there too, which the relay missed.

**Domain ids: 6 sites.**

| kind | site | form | shape read back by |
|---|---|---|---|
| concern | bus `post_concern` :4097, `send_to_peer` :4224 | `c-` + 12 hex | `model.is_concern_id` (model.py:2995) with `_CONCERN_ID_HEX = 12` (:2422). The mint sites spell `12` again. That is pinned by test_bus.py:8435 and test_peer_tools.py:484. |
| decision | bus `raise_decision` :5455 | `dq-` + 12 hex | nothing parses it. Tests pin the prefix (test_bus.py:5859, :6005). |
| task | bus `declare_task` :4796 | `t-` + **8** hex, only when the agent gives none | nothing |
| command | commandbus :896 | `cmd-<UTC stamp>-` + 12 hex | `command.is_valid_id` (command.py:103), which shares its constants with the mint by design (command.py:93-96) |
| channel | app `link_leads` :1681 | bare `uuid4()` | it must pass `model.is_channel_name`, because it is a directory name (channels.py:170-175). **No test pins that.** |

**Driver: 3 sites.**
- the session id, `uuid4()`, :1110;
- the parked call's pending id, `{session[:8]}-{tool_use_id or hex8}`, :2227;
- an anonymous call key, `anon-` + hex, :1764.

**A secret, not an id: 1 site.** `commandrun.mint_tag` uses `secrets.token_hex(32)` (:458).

**Can the reuse collide? No: the prefix is never read.**
- Every request id goes into `Bridge._requests`, one dict keyed by the whole string (bridge.py:273),
  and comes out by `effect.request_id` (:285).
- A grep of `pptmstr/` for prefix tests finds exactly one, `startswith("c-")` in `is_concern_id`.
- Request ids are not persisted, logged or shown. A grep for `request_id` in `log`, `boardlog`,
  `fleetsync`, `commandfile`, `boardfacts` and the LOG calls finds none.
- So two requests collide only if their 48 random bits match while both are outstanding. Prefix
  reuse adds nothing to that risk, and distinct prefixes would remove nothing from it. With n
  requests outstanding, the chance is about n²/2⁴⁹.
- If it did happen, `Bridge.ask` would overwrite the first future silently (bridge.py:273), and
  the first asker would wait until `abandon_all_requests` runs at shutdown.
- The same holds for concern ids. `ConcernPosted` keeps the first of two equal ids and drops the
  second without a word (store.py:833). That is recorded here, not proposed for fixing: the
  measurement names no occurrence (see "Not proposed").
- `boardgraph.DRY_RUN_REQUEST = "boardgraph-dry-run"` (:373) is a request id that never reaches the
  Bridge. It is read only from the effects of the pure `_apply` call it makes.

**Are ids compared across kinds? No.**
- `cross_kind.py` finds one function, `bus.wake_line` (:5805), that uses one string as a key into
  two kinds' maps (`decisions`, `commands`). Each lookup sits under `match wake.reason`.
- The other readers of `SessionWoken.subject_id` check `reason` before looking it up:
  `app._still_to_read` (:1310), `app._say_peer_wakes_spent` (:1265-1266), `bus.peer_wake_line`
  (:5872) and `_across_a_link` (:5796).
- The wake dedupe key is `(reason, subject_id)` (bus.py:5769).
- `subject_id` is the one field that carries several kinds, and `reason` is its discriminant, not
  the prefix.

**The one place a prefix is a namespace guard.**
- `withdrawal_notice_id` (`withdrawn/<task>`, model.py:3741) and `closing_notice`
  (`<operator party>closed/<task>`, :3711) put derived ids into the `concerns` map.
- `is_withdrawal_notice` and `is_closing_notice` read those prefixes.
- Bus-minted concern ids (`c-…`) can never start with either, whatever task id an agent picks.
- These are derived, not minted, so they stay with `Concern` in model and are out of `ids`' scope.

## (3) The proposal

### `ids.py`: what it requires is nothing from pptmstr

It holds:
- **The aliases:** `NodeId`, `ConcernId`, `TaskId`, `DecisionId`, `ChannelId`, `CommandId`. These are
  moved from model.py:45, :512, :1565-1566 and :2485, and from command.py:33. `model` and `command`
  import them.
- **One table**, one row per minted kind: its prefix and its width (T5: the table is the source).
- **The mints:**
  - `request()`;
  - `concern()`;
  - `decision()`;
  - `task()`;
  - `command(at: float)`, which takes the wall-clock time from its caller, so the module reads no
    clock;
  - `channel()`;
  - `session()`;
  - `pending(session_id, tool_use_id)`;
  - `call_key(call_id)`.
- **The shapes ids are read back by:** `is_concern_id` (from model.py:2995) and `is_command_id`
  (from `command.is_valid_id`, together with `ID_TIME_FORMAT`/`ID_HEX_CHARS`, so that command.py:93's
  reason, "cannot disagree about the form", moves with them).

**Request ids lose their per-tool letters: `request()` returns one form.**
- Nothing reads a letter, so each one is a claim no code checks.
- Keeping them as a table keyed by tool would preserve that unchecked claim, and it would put
  information into a correlation token, which STYLE §1 ("no token in the domain") warns against in
  the other direction.
- If a diagnostic ever needs to list outstanding requests by tool, the tool belongs as a field
  beside the future in `Bridge._requests`, not parsed back out of a key.
- No test asserts a request prefix (grep, *run*), so this changes nothing observable.
- The domain prefixes (`c-`, `dq-`, `t-`, `cmd-`) stay exactly as they are. They are persisted,
  shown to agents, and in two cases parsed.

**What stays out of `ids`, and why.**
- `commandrun.mint_tag` is a capability secret for the runner protocol, not an identifier. It keeps
  `secrets` as a named exception.
- `fake_driver`'s counters are a test double's deterministic ids.
- The notice ids are derived from a task id, not minted.

**Requirement and regularity.**
- `ids` is not pure in EoP §1.6's sense, because a mint reads entropy. It must not be added to
  `test_layers.PURE`.
- `model` imports `ids` only for aliases and shapes, and calls no mint.
- If the reviewer objects that a pure module now imports one that holds `uuid`, splitting `ids`
  into `ids` (aliases and shapes) and `minting` is the fallback. It costs one more name and buys
  nothing a test can see, so it is not proposed.

### `pptmstr/channels/`: one module per requirement

| Module | Requires (enforced by `test_layers`) | Holds |
|---|---|---|
| `__init__.py` | **nothing**: a docstring only, no imports, no re-exports (Rules §1) | T6: names the domain's pieces and which module holds each |
| `records.py` | `ids` only, so `model` can import it | `Charter` and its constants; the channel-name rule; `LINK_MEMBERS`; `LinkRefusal`; `Channel`; `EveryChannel`/`EVERY_CHANNEL`/`ChannelScope`; `PEER_WAKE_CHAIN`/`PeerWakes`; `MoveOwner`/`WhoseMove`; `ThreadMark`; `ThreadWakeHeld`; `PeerRefusal`; `PeerWithdrawRefusal`; `WITHDRAW_PEER_TOOL`; `peer_addressed`; `PeerIdentity`; and from store `_charter`, `_channel_as_linked`, `_peer_fields_hold` (they need only `Channel` and `ids.is_concern_id`). About 450 lines. |
| `projections.py` | `model` (a `Snapshot`); pure, no clock | the 26 `Snapshot` methods above as free functions over one `Snapshot` (the `board`/`boardgraph`/`digest` pattern); `PeerIndex`, `Thread*`, `ThreadSide`, `LinkThread`, `why_held`, `_thread_mark`; `effects.peer_identity`; `app._still_to_read`. About 750 lines. |
| `reducer.py` | `model`, `intents`, `effects`; pure; **only `store` imports it** | the store helpers above (about 800 lines). The `_apply` arms stay in `_apply`. |
| `text.py` | `model`, `effects`, `replies`; no SDK | B7's `peertext` group (its §2, 337 lines) plus `effects.peer_text`/`peer_forms` and `app._stopped`/`_sweep_notices`/`_not_written` (78) |
| `log.py` | the disk and a thread | today's `channels.py`, **whole** |

**What stays where it is, and why.**
- **The union members stay with their unions.**
  - What stays: the 12 channel intents (intents.py), the three peer effects plus
    `WakeReason.PEER_MESSAGE` (effects.py), and the three channel obligations (model.py).
  - Why: STYLE §1, "new variant → add it to the union". The union is read in one place.
  - The command domain already sets this precedent: its records live in `command.py`, while its
    intents and effects live in the union modules.
  - The obligations also need `ObligationKind` and are members of `Obligation`, so they cannot sit
    below `model`.
- **`_apply`'s nine arms stay in `_apply`.** It is the single audit point (store.py:451-452), and
  B9 owns that question.
- **What H+ → P rebuilds stays put:**
  - `Concern`'s peer fields and `is_peer`;
  - `boardlog.PeerPosted`/`PeerRetracted`;
  - `boardfacts._filed`'s peer branch;
  - `store._restored_concern`.
- **`channel_decision_readable` stays on `Snapshot`.** `decisions_readable_by` calls it
  (model.py:4833), so moving it would make a cycle.
- **`approval`'s tool names and `peer_channel` stay.** They are gate policy, so they belong to
  B5's `gate/`.
- **`fleetsync`'s arms stay.** They belong to a closed match over Intent.
- **Every `app` function that takes `AppState` stays.** `app` is the composition root.
- **The ui stays (657 lines).** Layer rule 3 forbids non-ui code importing ui, and the plan found
  that each ui widget attaches to its own pane, not to a shared suffix. `ui/link_form.py` is
  already the channel form.
- **Wakes in general stay.** `WakeReason`, `SessionWoken`, `_hold_wake`/`_send_wakes` and B7's
  `waketext` cover decisions, commands and concerns as well. Wakes are the plan's other homeless
  concept and need their own design. Only the peer half moves: `peer_wake_line`, `_peer_wake_said`
  and `_across_a_link` go to `channels/text`, and `waketext` imports them.

**`channels.py` moves whole and is not split here.**
- Its pure half (the facts, the codec, `restore`, `assess`, `judge`, `outcomes`) rides on
  `boardlog`'s `Record`/`Unrecognised`. B1 is replacing that with a journal substrate.
- `ChannelWriter` is being merged with `BoardWriter` by B3
  (2026-11-07-two-journals-share-one-serial-worker.md).
- Splitting the module now would decide both of those designs' interfaces ahead of them.
- After both land, the pure half becomes `channels/facts.py` and `log.py` keeps the disk and the
  thread. That is a step on its own (step 7).

**What the package costs.**
- `pptmstr/channels.py` and `pptmstr/channels/` cannot coexist, so step 2 rewrites every test
  binding of `channels` in one commit.
- The method-to-function move (step 5) is the largest patch: about 86 call sites in `pptmstr/` and
  about 164 in `tests/` (grep of `.name(`; `thread(` is overcounted by `ui/detail.thread`).
- What it buys: a reader of `channels/__init__` finds all of a domain whose code today sits in
  nine modules under three different words (channel, link, peer). The H+ → P refactor (below) then
  edits one module where it would have edited three.

### Move order, with each step checked against H+ → P

P is not scheduled anywhere (B9 found the same, by reading). What P changes is set out in
m2-judgement.md §(e):
- a `PeerMessage` type;
- `peer-posted` restoring into it, not into `Concern`;
- either a union in `Snapshot.concerns` (P1) or a `Snapshot.peers` map (P2);
- the peer branches of `ConcernPosted`/`ConcernWithdrawn`/`PeerSent`/`PeerInboxRead`;
- every projection that filters `concerns` on `is_peer`.

**The format is P's fixed point, and no step below touches it.**

Cold files go first and `app` last (churn.csv). The 90-day commit counts:

| File | Commits (90 days) |
|---|---|
| app | 53 |
| model | 30 |
| store | 30 |
| bus | 18 |
| intents | 18 |
| effects | 11 |
| commandbus | 4 |
| channels | 3 |
| bridge | 3 |
| command | 2 |

There is one domain per commit, every patch site moves in the same commit, and there are no shims.

| # | Step | Touches | Against H+ → P |
|---|---|---|---|
| 0 | Pins (next section) | tests only | none |
| 1 | `ids.py`, and the mints in `commandbus`, `command`, `bus`, `bridge`-adjacent callers | new file; command.py, commandbus.py, bus.py (mint lines only), model.py (aliases, `is_concern_id`) | **none.** `ConcernId` keeps its meaning, and a `PeerMessage` id is minted by the same `ids.concern()`. |
| 2 | `channels.py` → `channels/log.py`, with an empty `__init__` | channels.py, app.py:34 and 42 `channels.` refs, conftest.py:57/:589/:607, 4 test files (135 attribute reads, 14 imports) | **none.** The link log holds link facts only; peer messages are in board logs. |
| 3 | `channels/records.py` (below model); `model` imports it | model.py, effects.py (`PeerIdentity`), store.py (3 restore predicates); 58 model and 17 effects from-imports in tests, filtered to the moved names | **Enables P.** A `PeerMessage` whose fields are ids and enums can be born here. Nothing moved is reshaped. |
| 4 | `channels/reducer.py` (store helpers) | store.py (after r3-a4 and **after the B9 decision**) | **Textual only.** `_peer_waking`'s `PeerSent` case and `_owing_peer_mail` read `concerns`; P rewrites those lines wherever they are. **Not while a P branch is open.** |
| 5 | `channels/projections.py` (the `Snapshot` methods to functions) | model.py, store, bus, app, ui (about 86 sites), tests (about 164) | **The step P benefits from most.** `peer_inbox_of`, `peer_unread`, `awaiting_answer`, `questions_unread` and the others are exactly P's "filters concerns on `is_peer`" sites, and after this they are in one module. mypy (strict, over `pptmstr/`) flags every stale `snap.method(` in `pptmstr/`; in tests they fail at run. **Not while a P branch is open.** |
| 6 | `channels/text.py` | bus.py, effects.py, app.py; **done as B7's `peertext` step, not separately** | none. The text reads `Concern` fields that P keeps under the same names. |
| 7 | after B1 and B3: split `channels/log.py` into `facts.py` (pure) and `log.py` | channels/ only | none |
| 8 | driver's mints (`session`, `pending`, `call_key`) to `ids` | driver.py (hot: 38) | none |

If the lead schedules P before steps 4 and 5, P lands first and those two steps take its result.
Neither order changes the log.

### Against B9 and B7 (a conflict for the lead)

**B9 proposes `store/peers.py` and `store/waking.py` for the same roughly 800 lines that step 4
puts in `channels/reducer.py`** (2026-11-07-apply-dispatches-from-one-table.md, the layout table and
"Other collisions", which asks the lead to decide).

Both layouts keep the audit point under B9's P2 pin ("only `_apply` imports an arm module"), as long
as the pin names the module wherever it lives. The difference is what a reader navigates by:
- The 2026-11-06 plan recorded **domain, not layer** ("a global core/shell split" is refuted;
  "layer is expressed *within* a domain package"). That favours `channels/reducer.py`.
- B9's layout is per-entity *within* the store. That favours `store/peers.py`.
- The same tension already stands between B5 (`board/`) and B9 (`store/tasks.py`). Whatever the
  lead rules for channels should be ruled for board too.

Recommendation: **domain-first for channels.** It is the only entity with a log, a projection
layer, a text layer and a reducer layer of its own, and the plan names it as homeless. B9's other
entity modules are unaffected. If the lead rules for B9, step 4 becomes B9's move, and the channel
package is records, projections, text and log, with its `__init__` pointing at `store/peers.py`.

**B7 names `peertext` as a flat module.** This design puts the same group at `channels/text.py`.
That is a one-line naming decision, the same ruling as above, and it is not a second move.

**B1 and B3 both edit `channels.py` in place.** Step 2 renames that file. None of these three may
be on the board together.

## (4) Pins written first, and the patch sites

Each pin is mutation-tested against the current line (STYLE §2), and goes green on HEAD before its
step.

**Ids**
- **I1. A minted channel id is a name a link log can live under.** Call `app.link_leads` and
  assert the emitted `channel_id` passes `model.is_channel_name` and makes a valid `LinkKey`. This
  is missing today.
  - *Mutation:* `uuid4().hex.upper()` must fail it.
- **I2. Every mint has the shape it is read back by.**
  - The concern and command shapes are already pinned: test_bus.py:8435, test_peer_tools.py:484,
    test_commandbus.py:222/:271.
  - Add the decision (`dq-` + 12 hex) and the agent-less task (`t-` + 8 hex) through their handlers.
  - After step 1, the same assertions run against `ids.*` directly.
- **I3. `uuid` and `secrets` are imported only by `ids`** (a `test_layers` rule, written in that
  file's style).
  - The exceptions are exact, and a stale exception fails. At HEAD they are bus, commandbus, app,
    driver (`uuid`) and commandrun (`secrets`).
  - Each step deletes its line. After step 8 only `commandrun` remains, with its reason.
  - This is the "one owner" claim made checkable.
- **I4. `ids` imports nothing from pptmstr and calls no clock** (`_CLOCKS`). This follows the same
  rule as `naming` (rule 6).

**Channels**
- **C1. Layer rules for the package** (`test_layers`):
  - `pptmstr.channels` (`__init__`) imports nothing;
  - `channels.records` imports nothing from pptmstr but `ids`;
  - `projections`, `reducer` and `text` import no disk reader and are added to `PURE` with their
    docstring claims;
  - only `app` imports `channels.log`.
  - *Mutation:* an `__init__` that re-exports `log` must fail, because it would make `model` reach
    the disk through `records`.
- **C2. `channels.reducer` is imported only by `store`.** This is B9's P2 extended by name.
- **C3. The behavioural suites stay green, unchanged, at every step.** Run on the HEAD export
  (*run*), the 13 files below give **1247 passed, 6 skipped**:
  - test_channels;
  - test_link, test_link_form, test_link_probe, test_link_wiring;
  - test_peer_concerns, test_peer_surfacing, test_peer_tools, test_peer_wakes;
  - test_channel_decisions;
  - test_wake_asker;
  - test_layers;
  - test_boardfacts.

  These include H+'s three acceptance pins, which guard what B6 leaves alone:
  - the kind pin, in test_boardfacts/test_boardlog;
  - the round trip;
  - `test_the_two_inboxes_split_the_waiting_mail_with_nothing_in_both` (test_peer_concerns.py:250).

**Patch sites by step** (*run*, `patch_sites.py`):

| Step | Patch sites |
|---|---|
| 2 | conftest.py:57 (import), :589 (`channels.root` default), :607 (`monkeypatch.setattr(channels, "root")`, the only setattr on a moving channel name in `pptmstr`'s own module); 135 `channels.X` reads and 14 `from pptmstr.channels import` lines in 4 test files; app.py:34 plus 42 `channels.` references |
| 3 | from-imports of the moved model and effects names (a subset of 58 model and 17 effects lines); `stated_provenance`/`Provenance` import `Charter` and `_channel_refusal` from `records` |
| 4 | none in tests by code. Six docstring citations name `store._peer_waking`/`_still_owed`/`_moved`/`_peer_row` (test_app.py:2688, :2745; test_approval.py:2774; test_peer_tools.py:1963; test_peer_wakes.py:8, :1696), and they are updated with the move (D-b, T8). |
| 5 | about 250 method call sites (above). No monkeypatch or dotted patch targets a `Snapshot` method. |
| 6 | B7's list for `peertext`. Plus `app._stopped`/`_sweep_notices`/`_not_written`, which no test names directly. |
| ui | `monkeypatch.setattr(inbox, "channel_marker")` and `(session_menu, "link_peers")` do not move |

**Live plans that cite `channels.py`** get the D-b rename table in the commit of step 2:
- 2026-11-06-modules-are-named-for-what-they-require.md;
- 2026-10-18-building-the-inter-lead-channel.md;
- the three 2026-11-07 siblings that cite it.

There are also 13 docstring citations of `channels.`/`channels.py` inside `pptmstr/`.

## (5) What would refute this

- **A cycle the scan did not see.**
  - `deps.py` follows in-module calls only.
  - If any record listed for `records.py` needs a `model` type through a default value, a
    `ClassVar` or a string annotation evaluated at runtime (`get_type_hints`, which `channels.py`
    uses at :227 on its own facts), step 3 fails at import.
  - Check: build step 3 in `$TMPDIR` and import `pptmstr.model` first.
- **A projection that a non-channel `Snapshot` method reaches by attribute.** The scan found three
  inward edges and handled them:
  - `questions_unread` moves with the set;
  - `decisions_readable_by` keeps `channel_decision_readable` behind;
  - `inbox_of` reads `is_peer`.

  A fourth edge would cycle.
- **The lead rules B9's store-first layout for every entity.** Step 4 then belongs to B9, and the
  package shrinks to records, projections, text and log. The rest stands.
- **Someone reads a request id's prefix.** Before `request()` drops the letters, grep the scripts
  and the transcript and log formats again. This scan covered `pptmstr/` and `tests/`, not
  `scripts/` or anything outside the repo.
- **A P branch is opened before steps 4 and 5.** Those steps then wait. Done concurrently, they
  collide textually.
- **The 48-bit argument is wrong about the population.**
  - If anything keeps a request id outstanding indefinitely, the population grows without bound:
    a future that is never settled and never abandoned would do it.
  - The argument still holds, but `Bridge.ask`'s silent overwrite becomes worth a refusal.
  - Check by measuring `Bridge.asking_count` over a long session.

## Not proposed

- **`Bridge.ask` refusing a duplicate id, and `ConcernPosted` saying a dropped duplicate.** Both are
  silent today. A default where the honest answer is a refusal is a STYLE §3 smell. But no run has
  shown a collision, and the probability is about n²/2⁴⁹. Recorded, not boarded.
- **Grouping the six channel `Snapshot` fields into one sub-record.** This would reshape the world
  value, not move code, and where P2's `peers` map lives is P's decision.

## What was not run

- No design step was built, and nothing under `pptmstr/`, `tests/` or `scripts/` was changed.
- The full `make check` was not run. Only the 13 channel suites ran, on a `git archive` export of
  66af79e using the worktree `.venv`. The 6 skips were not inspected.
- mypy was not run on any proposed layout. The cycle claims come from the AST coupling scan, not
  from importing a rebuilt tree.
- The call-site counts for step 5 are a grep of `.name(`, and they overcount `thread`.
- The line totals are def spans, which are lower bounds. The model split (records about 450,
  projections about 600) is summed by hand from the scan rows.
- The "no prefix is read" claim covers `pptmstr/` and `tests/` only.
- The SDK's own use of the session id was not checked: `ids.session()` must keep `str(uuid4())`
  exactly, because a resume passes the SDK's id back through.
- B7's and B9's designs were read for their overlap with this one, not reviewed.

## Names moved out of approval.py

The text above cites these names in `approval.py`. B5's leaves step
(`planning/2026-11-07-board-and-gate-packages.md`, Order step 3) moved them to
`pptmstr/gate/`; the tool tables went public as they moved. A line number cited
above is `approval.py`'s when the text was written.

| Cited as | Now |
|---|---|
| `approval.peer_channel` | `gate.tools.peer_channel` |

## Names moved by B5's last step

The text above cites these names in `approval.py`. B5's last step
(`planning/2026-11-07-board-and-gate-packages.md`, Order step 4) split what was
left of `approval.py` into `pptmstr/gate/` (`rows`, `reach`, `classify`, `park`)
and deleted it. A line number cited above is the old file's, when the text was
written.

| Cited as | Now |
|---|---|
| `approval.py` | deleted; its parts are `pptmstr/gate/rows.py`, `reach.py`, `classify.py` and `park.py`, beside the leaves |
