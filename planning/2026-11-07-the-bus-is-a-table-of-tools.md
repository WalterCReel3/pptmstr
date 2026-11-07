# The bus is a table of tools, and its words live by domain

**Dated:** 2026-11-07 · **Status:** design only (Round 3, B7 of
`2026-11-06-modules-are-named-for-what-they-require.md`). Nothing is built. One open decision,
D-B7a, is under "Open". · **Asked by:** the lead, as board task `r3-b7`, under the operator's
approval of the build rounds (brief entry `001-build-rounds.md`). · **Evidence:** an AST scan of
`pptmstr/bus.py` at 66af79e, a scan of every module, test and script that reads `bus`, a read of the
SDK's `create_sdk_mcp_server` (claude-agent-sdk 0.2.167, the worktree `.venv`), and one test run.

A line marked *run* was executed. Everything else is **read**. Line numbers are at 66af79e, and
each one also names its symbol.

**Baseline** (run, 66af79e): `pytest tests/test_bus.py tests/test_layers.py
tests/test_read_digest_unchanged.py tests/test_templates.py tests/test_peer_tools.py
tests/test_peer_wakes.py` gives 1157 passed. `make check` was not run.

## The answer in one paragraph

`bus.py` is 5,924 lines, and three things make up nearly all of it:
- the 19 bus tools, each a closure inside `build_server` (595 lines of name, description and
  schema, 972 lines of handler body);
- 2,973 lines of module-level functions that return text;
- about 400 lines of call-argument readers.

The renderers are already pure. Only two module-level functions touch anything outside their
arguments: `_premises_text`, which reads the brief from disk, and `_park`, which uses the bridge.
The renderers also form a DAG that has one sink. So the move is mechanical, in two stages:
1. Cut the renderers verbatim into seven pure `*text` modules. Each joins `test_layers.PURE`.
2. Lift the 19 closures to module level as `(session, bridge, node, args) -> str` handlers. Each
   becomes one `Tool` record, and `build_server` becomes a fold over two tables: the bus's own
   `TOOLS`, and the `command_tools` it is handed.

The safety net exists for names, descriptions and schemas: the sha256 pin that r2-cycle-bus added.
It does **not** exist for reply text or for registration order. Both pins are owed before the first
move, and they are step 0 below.

## 1. The map (run: AST scan)

The scan parses `bus.py`. It records every top-level definition, every `@tool` closure inside
`build_server` (3922-5641), and the five nested helpers. For each handler it takes the transitive
closure of top-level names it reaches. Each definition is then assigned to the domains whose
handlers reach it. Sizes are definition spans; the assignment table in §2 also counts the comment
block directly above each definition.

### The handlers

Every handler's first statement is `_sender(args)` (run). That fact is what lets the fold supply
the sender without reordering anything a handler does.

| Tool | Description + schema | Body | Nested helpers | Top-level names reached |
|---|---|---|---|---|
| `post_concern` | 4022-4061 (40) | 64 | – | 13 |
| `read_inbox` | 4127-4134 (8) | 29 | – | 12 |
| `send_to_peer` | 4165-4213 (49) | 55 | – | 29 |
| `read_peer_messages` | 4270-4294 (25) | 29 | – | 21 |
| `withdraw_peer_message` | 4325-4354 (30) | 50 | – | 20 |
| `claim_task` | 4406-4418 (13) | 52 | `board_row` | 18 |
| `read_board` | 4472-4491 (20) | 38 | – | 39 |
| `read_task` | 4531-4558 (28) | 66 | – | 47 |
| `read_digest` | 4626-4635 (10) | 12 | – | 34 |
| `why_blocked` | 4649-4666 (18) | 10 | `board_world` | 21 |
| `ready` | 4678-4687 (10) | 7 | `board_world` | 17 |
| `declare_task` | 4696-4785 (90) | 138 | `board_ids`, `board_world` | 41 |
| `amend_task` | 4925-4967 (43) | 78 | `board_row` | 25 |
| `complete_task` | 5047-5094 (48) | 83 | `board_row`, `withdrawn_because` | 31 |
| `release_task` | 5179-5188 (10) | 25 | `withdrawn_because` | 18 |
| `withdraw_task` | 5215-5240 (26) | 69 | `board_row`, `withdrawn_row` | 18 |
| `raise_decision` | 5311-5401 (91) | 117 | `board_ids` | 32 |
| `read_decisions` | 5520-5548 (29) | 23 | – | 29 |
| `withdraw_decision` | 5573-5579 (7) | 27 | – | 19 |

The five nested helpers (3936-4020, 85 lines) close over `bridge` alone. `withdrawn_because` also
calls `board_row`.

The handlers use `session` for only five things:
- `resolve_role` and `role_status` (in `post_concern` and `complete_task`);
- `role_of` (in `read_inbox` and `read_decisions`);
- `brief`, through `_premises_text`'s `getattr`;
- `bridge` and `contained`, which `build_server` reads.

The task's brief said "about 20" handlers. There are 19, plus the three command tools on a
contained server.

### The renderer groups

Each group is named for the domains whose handlers reach it. `[text]` means a sync function that
returns `str` or `list[str]` and names no bridge, clock, uuid or brief.

| Reached from | Lines | Defs | What |
|---|---|---|---|
| board only | 1,213 | 35 | rows, task reads, write replies, claim/declare refusals; includes 5 argument readers (120 lines) |
| decisions only | 506 | 14 | refusals, the whole and listed decision, `_decisions_text`; includes 2 argument readers |
| peer only | 352 | 9 | messages, links, refusals; includes `_stated_peer_message` (53) |
| digest only | 263 | 9 | `_digest_text`, `_bounded_digest_text` and parts |
| wake (from `app`, not a tool) | 173 | 6 | `wake_message`, `wake_line`, `peer_wake_line` |
| board+decisions+digest+graph+peer | 168 | 3 | `_refusal_text`, `_duplicate_id_text`, `_held_live_text` |
| board, non-text | 93 | 6 | `_undeclared`, `_agent_notes`, `_delimited_task`, `_unshown`, `_behind`, `_loop_has_a_gate` |
| graph only | 85 | 2 | `_why_blocked_text`, `_ready_text` |
| board+graph | 69 | 4 | `_shared_note`, `_hop_state`, `_root_needs`, `_chain_lines` |
| concerns only | 41 | 2 | `_operators_notice`, `_withdrawal_notice_lines` |
| board+digest | 48 | 3 | `_closing_reason`, `_rest`, `_delimited_note` |
| smaller shared pieces | ~90 | ~15 | `_legend`, `_edited_note`, `peer_said`, `_lead_of`, `_linked_now`, `_length_refusal`, constants |

All `[text]` definitions together come to 2,973 lines (run). That corroborates the plan's "about
3,000". The relayed per-group split (board ~1,118, decisions ~731, refusals ~411, wake ~299, digest
~261) came from a different partition, which the plan does not record, and was not reproduced.
Treat the table above as the current figures: the scan regenerates it.

**Two functions are not pure, and both stay in `bus`.** `_premises_text` reads the brief's entry
count from disk (bus.py:657, documented as deliberately on the asyncio thread). `_park` uses the
bridge.

### Who reads `bus` from outside (run: AST scan of pptmstr/, tests/, scripts/)

- **Application.**
  - `driver` reads `CALLED_AT_KEY`, `EDITED_KEY`, `FROM_KEY`, `SERVER_NAME`, `build_server` and
    `offered_tools`. All of them stay in `bus`.
  - `app` reads `wake_message` and nothing else. It imports the SDK-carrying module for one pure
    function.
- **Monkeypatch sites on `bus`: zero.** No `setattr(bus, …)` and no `"pptmstr.bus.…"` strings.
  Rule 1 of the plan (no shims for names that tests patch) is therefore not engaged.
- **Implicit re-exports.** The thing that *is* engaged is that tests read names through `bus` which
  `bus` only imports:
  - `_one_line` (test_board_pane, test_effects);
  - `_QUOTE` and `_ANSWER` (test_command_rows, test_decision_view);
  - `between_quotes`, read as a wiring `is` check (test_effects:134);
  - `PEER_TITLE_CHARS` (test_peer_tools);
  - `WAKE_SAID` and `WAKE_SAID_SUBAGENT` (test_wake_asker);
  - `MAX_ID_CHARS`, `MAX_PATH_CHARS`, `MAX_PROPERTY_CHARS` and `_MAX_BOARD_CHARS` (test_bus).

  None of these fails silently: a name that leaves `bus` raises `AttributeError` or `ImportError`.
  They are still patch sites.
- **Tests.** 21 test files read `bus`. `test_bus.py` has 362 tests, and 269 of them go through the
  live server by tool name. Only 12 call a renderer directly, about 190 lines. In `test_peer_tools`,
  9 of 97 tests call a peer renderer directly.

## 2. The proposal

### 2a. The words: seven `*text` modules, cut verbatim

Each module below has a module docstring that opens "Pure: no SDK, no clock, no IO". It is
enrolled in `tests/test_layers.py`'s `PURE` with that claim, as `replies` is.

| Module | Lines (with comments) | Holds | Imports, besides model/board/boardgraph/digest/effects/replies |
|---|---|---|---|
| `boardtext` | 1,468 | Board rows, `read_task` parts, write replies, `_refusal_text`/`_claim_refusal_text`/`_declare_refusal_text`, the closing and withdrawal words, `_legend`, `_edited_note`, the `_MAX_*_CHARS` row bounds | none: the sink |
| `decisiontext` | 492 | Everything reached only from the three decision tools, plus `_DECISION_FRAME_CHARS` and `_REVERSIBLE_SAID` | `boardtext`, `peertext` |
| `peertext` | 337 | Peer messages, links, refusals, `peer_said`, `_lead_of`, `_linked_now`, `_peer_wake_said`, `_PEER_LEGEND`, `_CHARTER_LEGEND` | `boardtext` |
| `digesttext` | 279 | `_digest_text`, `_bounded_digest_text` and parts | `boardtext` |
| `graphtext` | 234 | `_why_blocked_text`, `_ready_text`, `_dry_run_text`, the chain lines, `_loop_has_a_gate` | `boardtext` |
| `waketext` | 206 | `wake_message`, `wake_line`, `peer_wake_line`, `WAKE_*` | `peertext` |
| `concerntext` | 41 | `_operators_notice`, `_withdrawal_notice_lines` | `boardtext` |

The edges between them were computed (run) from every name each definition reads. The graph is
acyclic, with `bus` above all seven. That holds once one constant is moved: `_REVERSIBLE_SAID` goes
to `decisiontext`, because both the renderer and the `_stated_reversible` argument reader in `bus`
read it. Left in `bus`, it is the only back edge.

**What stays in `bus` (~2,285 lines before stage 2):**
- what a call may carry: the `_stated_*` readers, `_length_refusal`, `_property_refusal`,
  `_listed_refusal`, `_kept`, `InvalidBasis`, `InvalidStanding`, `_PeerMessage`, the `*_KEY`
  stamps, `MAX_TEXT_CHARS`, `MAX_LINE_CHARS`, `MAX_LISTED`;
- `_sender`, `_called_at`, `_text`, `_park`, `_outcome`, `_premises_text`;
- the registry names;
- the handlers.

These are a tool's input contract, which its schema states. They are not words a tool says, so
they stay with the handlers they guard.

**On "following `commandtext`".** The task says the renderers move "following `commandtext`", but
that is not the precedent it sounds like (read):
- `commandtext` renders the *command string* for the operator: `visible`, `invisibles`,
  `breakdown`.
- The command tools' *reply* words (`commands_text`, `_command_whole` and their parts, about 640 lines at commandbus.py:203-840) live in
  `commandbus` itself, beside its handlers.

So `commandtext` sets the precedent for the module shape: a pure, per-domain text module. It does
not set one for the split. B7 is the first place where reply words are separated from their
handlers. Whether `commandbus` should split the same way is not in B7's scope; see "Open".

**What it buys, besides size.** None of the seven imports the SDK. Three things follow:
- `app` stops importing `bus` (its only use is `wake_message`, which moves to `waketext`).
- The UI copies of the marks and `_one_line` that Round 2's review listed become importable from
  SDK-free modules.
- `approval.py`, which is SDK-free by recorded decision (approval.py:124-128), can cite the
  renderers it reasons about by a module it could import.

**B5 coordination** (read: `2026-11-07-board-and-gate-packages.md`). B5's `board/` holds `rows`,
`words`, `graph` and `digest`. Its `words` is `board.py`'s four label functions (`basis_label`,
`property_label`, `not_run_label`, `incident_marker`), *not* the bus's reply words. B5 does not
place the 2,973 renderer lines at all, so the two designs do not collide on any definition. They
do collide on files:
- B5's step 1 rewrites `bus`'s `from .board import …` lines;
- the B7 text modules inherit those imports.

Whichever lands second rebases. Whether `boardtext`, `graphtext` and `digesttext` then move under
`board/` (as `board/replies*` or similar) is a question for that second commit. It is not decided
here.

### 2b. The tools: one record each, and `build_server` a fold

1. **One record type, shared.**
   - `Tool` moves from `commandbus` to `replies`, made generic in the session it is handed:
     `Tool[S]` has `name`, `description`, `schema: dict[str, Any]`, and
     `handler: Callable[[S, Bridge, NodeId, dict[str, Any]], Awaitable[str]]`.
   - `replies` imports `Bridge` under `TYPE_CHECKING` only, so it stays pure.
   - `commandbus.TOOLS` becomes `tuple[Tool[Roles], ...]`, edited in the same commit, with no
     re-export.
   - `OfferedTool` (bus.py:3897) is then unnecessary. It existed so that `bus` could type
     `commandbus`'s record without importing `commandbus`, and a record in `replies`, which both
     already import, does that job. It is deleted.
2. **A narrow session Protocol in `bus`**, as `commandbus.Roles` is. `Team` has `bridge`,
   `contained`, `brief: str | None`, `resolve_role`, `role_status` and `role_of`.
   - `AgentSession` satisfies it.
   - The test fakes (`_Session`, `_Contained`, `_Mute`) are checked against it by mypy only if
     tests are typechecked. `make check` runs `mypy pptmstr` and not `typecheck-all`, so the fakes
     are held only at runtime.
   - `_premises_text` keeps its `getattr(session, "brief", None)`, because `_Contained` has no
     `brief` and the behaviour of a missing brief is pinned.
3. **Lift the five nested helpers** to module-level functions that take `bridge` first:
   `board_row`, `withdrawn_row`, `withdrawn_because`, `board_ids` and `board_world`. Each is a
   transcription.
4. **Lift each handler** to module level as
   `async def post_concern(session: Team, bridge: Bridge, me: NodeId, args) -> str`:
   - delete its first line (`me = _sender(args)` or `sender = …`); the fold supplies `me`;
   - unwrap every `return _text(x)` to `return x`.

   Both changes are mechanical, and the AST check under §5 covers them.
5. **One ordered table:** `TOOLS: tuple[Tool[Team], ...]`, in today's registration order
   (bus.py:5608-5628). The descriptions and schemas are moved as written.

   Three schemas are not `_schema(...)`, and each must stay as written:
   - `read_inbox`, `read_digest` and `ready` pass `{}`;
   - `release_task` passes `{"task_id": str}`.

   The SDK normalises non-JSON-Schema dicts itself (`_build_input_schema`: a dict with no
   `type`/`properties` becomes `{"type": "object", "properties": …, "required": [all keys]}`). So
   rewriting any of these through `_schema` would change the wire entry, and the digest pin would
   catch it.
6. **The fold:**
   ```python
   def build_server(session: Team, command_tools: Sequence[Tool[Roles]]) -> Any:
       offered = (*TOOLS, *(command_tools if session.contained else ()))
       return create_sdk_mcp_server(
           SERVER_NAME, "1.0.0", [_registered(spec, session) for spec in offered]
       )
   ```
   `_registered` is today's `offered` closure (bus.py:5633-5638), unchanged:
   `_text(await spec.handler(session, session.bridge, _sender(args), args))`.
   `session.contained` is still read unconditionally, so `_Mute` still raises `AttributeError`.
7. **The registry derives from the table** where it can:
   - `BUS_TOOLS` is derived: `frozenset(qualified(t.name) for t in TOOLS) - PEER_TOOLS`.
   - `PEER_TOOLS` stays three spelled names, with a new assertion that they are in `TOOLS`.
   - `COMMAND_TOOLS` stays spelled and pinned. `bus` still does not import `commandbus`, and
     `offered_tools` is called by the driver's gate without the tool list.

   The comment at bus.py:5643-5646 argues for a separate `BUS_TOOLS` list: "a list in one place
   is a cheaper way to not have it" go missing. With a table, the registration *is* that list, so
   deriving `BUS_TOOLS` removes the second place rather than adding one. This is T5 from the plan.

   `templates.BUS_TOOL_NAMES` and approval's `_BUS_*` spellings stay as they are, deliberately
   duplicated and pinned (test_templates.py:330-351, test_approval.py:1215).

After stage 2, `bus` is about 2,200 lines: the table (descriptions and schemas, ~600), the handlers
(~970), the call layer (~400) and the registry.

## 3. What the static graph cannot see

- **Tools are called by a string.**
  - The CLI sends `mcp__pptmstr__<name>`. The SDK dispatches by `tools_by_name[tool_name]` and
    answers an unknown name with an `isError` result, not an exception (SDK `run_tool`, read).
  - No Python reference runs from any caller to any handler. A record dropped from `TOOLS` is a
    tool that has silently gone, and an import graph shows nothing missing.
  - The same is true of `call_tool(server, "<name>", …)` in tests: 536 `.call` sites in
    `test_bus.py` alone.
- **The names are spelled outside `bus`:**
  - `templates.BUS_TOOL_NAMES` and `COMMAND_TOOL_NAMES`;
  - `approval._BUS_POST`, `_DECLARE`, `_AMEND`, `_PEER`, `_DECIDE`, `_WITHDRAW` and
    `_WITHDRAW_PEER`;
  - `model.WITHDRAW_PEER_TOOL`;
  - `fake_driver.POST_CONCERN` and `DECLARE_TASK`;
  - `ui/inbox._POST_CONCERN` and `_DECLARE_TASK`;
  - `ui/widgets`' probe row;
  - prose in every description and in many replies ("call read_commands", `read_task(task_id=…)`).

  None of these moves under B7, and none is checked by an import.
- **The gate stamps by name.** `driver` stamps only the names in `offered_tools(contained)`
  (driver.py:2187). A tool registered but missing from the registry reaches its handler unstamped,
  and `_sender` raises `UnstampedCall`, which the SDK turns into an `isError`. That fails loudly,
  but only when called.
- **Registration order.** The SDK builds `tools/list` from the list it is handed, in order (read).
  No test pins the order: the digest pin serialises a dict keyed by name with `sort_keys=True`.
  Whether order changes model behaviour was not measured. It does change the prompt prefix.
- **Docstring citations.** `bus.<name>` appears in 38 places in `pptmstr/` outside `bus.py` (store,
  effects, model, approval, digest, app, driver, commandbus, ui/fleet_view). Six live planning
  files cite moved names (listed in §4). One citation is already stale: `effects.py:622` cites
  `bus.wake_lines`, which does not exist.

**The safety net, as it stands (read; the first two run at baseline):**

| Pin | Holds | Does not hold |
|---|---|---|
| `test_every_server_announces_the_tools_it_did_byte_for_byte` (test_bus.py:7930) | the registered name set, both containment states; sha256 of every name, description and full wire entry | order |
| `test_the_replies_laid_out_with_the_shared_marks_are_unchanged` (:7969) | three whole replies (two `read_commands`, one `withdraw_decision`) and the `replies` helpers | the other 18 bus tools' replies |
| `test_the_command_tools_are_announced_as_their_own_module_describes_them` (:8043), `…no_containment_never…` (:8030), offered/announced checks (:1116-1162, :1445), `test_every_tool_accepts_the_call_its_own_description_documents` (:1458) | the registry and the server agree, and each tool accepts its documented minimal call | — |
| `test_read_digest_unchanged.py` | `_bounded_digest_text` output | — |
| 269 live-wire tests in `test_bus.py`, plus the peer/wake/decision suites | behaviour, mostly by substring | byte identity of reply text |

The task's must-not-break is "reply text stays byte-identical". As the table shows, that is
pinned for one bus tool, not nineteen. Step 0 closes the gap before anything moves.

## 4. Move order, patch sites and tests

One commit per step. Each step ends with `make check`, compared against the baseline. Docstring
citations are fixed, and live-plan rename tables appended (D-b), in the same commit as the move
they follow.

0. **Pins first; no move.**
   - (a) An order pin: `list(announced) == [...]`, for both containment states.
   - (b) A reply corpus pin. Drive each of the 19 tools through `_live_bus` along every reply arm
     the existing fixtures reach: success and each refusal. Normalise ids and ages as the existing
     sample does (bus.py ids are `uuid4`-minted, and ages are wall-clock), and hold the corpus by
     sha256. Mutation-test it: change one character of one renderer and confirm that it fails
     (STYLE §2).
   - (c) Retarget `test_effects.py:134`'s wiring check (`bus.between_quotes is
     effects.between_quotes`) to each module that will call it.
1. **`boardtext`** (the sink).
   - Patch sites: `test_bus` (`_board_line`, `_refusal_text`, `_claim_refusal_text`,
     `_duplicate_id_text`, `_already_behind_text`, `_auto_depends_text`, `_landed_behind_text`,
     `_closing_reason`, `_rest`, `_held_live_text`, `_CLOSING_LEGEND`, `_CLOSED_UNDER_YOU`,
     `_WITHDRAWN_UNDER_YOU`, `_MAX_*_CHARS`), `test_boardlog` (the four `_MAX_*_CHARS`),
     `test_board` (`_board_text`), and the re-export reads that this commit makes fail.
   - Citations: approval.py:337, model.py:1673.
2. **`peertext`.**
   - Patch sites: `test_peer_tools` (`peer_said`, `_PEER_LEGEND`, `_peer_refusal_text`, `_lead_of`,
     `_peer_messages_text`, `_cited_text`, `_CHARTER_LEGEND`; `PEER_TITLE_CHARS` → `effects`),
     `test_peer_wakes` (`_peer_wake_said`), `test_peer_surfacing` (`_peer_messages_text`),
     `test_channel_decisions` (`_PEER_LEGEND`).
   - Citations: effects.py:199, model.py:2866, approval.py:297/414.
3. **`digesttext`.**
   - Patch sites: `test_bus` (`_digest_text`, `_MAX_DIGEST_LINE_CHARS`), `test_read_digest_unchanged`,
     `test_peer_surfacing` (`_waiting_line`).
   - Citations: digest.py:182/681, approval.py:325/364.
4. **`graphtext`.**
   - Patch site: `test_bus` (`_CUT_NOTE_ROOM`).
   - Citations: approval.py:426.
5. **`concerntext`.** No outside readers.
6. **`decisiontext`**, together with `_REVERSIBLE_SAID`.
   - Patch sites: `test_bus` (`_answer_lines`, `_ANSWER_LEGEND`, `b._DECISION_FRAME_CHARS`),
     `test_channel_decisions` (`_decision_refusal_text`, `_decisions_text`).
   - Citations: commandbus.py:153, ui/fleet_view.py:55, approval.py:363.
7. **`waketext`.**
   - Patch sites: `app.py:1218` (`bus.wake_message`; `app` drops `bus` from its imports at line
     38), `test_peer_wakes`, `test_peer_surfacing`, `test_wake_asker` (`wake_message`,
     `wake_line`, `WAKE_*`, `_titled`; `WAKE_SAID*` → `replies`), `test_peer_tools` (`wake_line`).
   - Citations: store.py:2928/2932/3209/3213, driver.py:3158, app.py:1164/1283, model.py:3674,
     and effects.py:622's stale `bus.wake_lines`.
8. **The table** (§2b). Patch sites:
   - `commandbus` (`Tool` → `replies.Tool`);
   - `driver.py:2501` (unchanged call, retyped argument);
   - `test_commandbus`, if it names `commandbus.Tool`;
   - `test_bus.py:8043`'s comment about `OfferedTool`;
   - `bus`'s module docstring, which today says the handlers are closures.

Steps 1-7 also add each module to `test_layers.PURE`. Steps 1-7 are independent of step 8, and
step 8 can land on its own.

**Live planning files that cite moved names**, which get the D-b rename table:
- `2026-09-03-orca-made-the-opposite-bet-on-the-same-tradeoff.md` (`_board_line`; and
  `_premises_text`, which does not move);
- `2026-10-12-session-control-and-the-inter-team-record-in-one-sequence.md` (`_board_line`);
- `2026-10-12-wiring-the-inter-team-record.md` (`_change_lines`);
- `2026-10-17-two-leads-in-one-window-share-a-channel.md` (`_withheld`);
- `2026-10-18-building-the-inter-lead-channel.md` (`WAKE_DECISIONS_NAMED`);
- `2026-11-06-modules-are-named-for-what-they-require.md`.

`2026-08-14-a-role-runs-one-agent.md` cites `bus.py:NNN` by line eight times. Every step
invalidates those line numbers whether or not it moves the cited symbol. The D-b table covers it by
module name.

**Test-file moves: none.** 269 of `test_bus.py`'s 362 tests go through the wire by tool name,
which is the very safety net this change relies on, and they stay where they are. The 12 direct
renderer tests in `test_bus.py` and the 9 in `test_peer_tools.py` have their imports rewritten and
are not moved. Moving them would put a second kind of diff into a commit whose review rests on
being nothing but cut, paste and import rewrite. A `test_boardtext.py` can collect them later, as
a commit of its own.

## 5. What would refute this, and what was not run

**Refuted if any of these holds:**
- **Any pin from step 0, or the digest pin, moves after a move commit.** The move was then not
  verbatim. The fix is to the move, not the pin.
- **The verbatim check fails.** For every moved definition, `ast.dump` (without line attributes)
  must be equal at HEAD and after the move. For the handlers, it must be equal after deleting
  statement 1 and unwrapping `_text(...)` returns. And every global name a moved definition reads
  must resolve in its new module to *the same object* it resolved to in `bus` at HEAD (`is`). The
  hazard is a name the destination already defines differently: `commandbus` and the new
  `decisiontext` both have a `_headline`, for instance. This is a script for the builder to write
  and run. It is a probe and is not kept as a test.
- **`test_layers.PURE` fails for a new module.** The scan's purity test was by name: no `bridge`,
  `time`, `uuid`, `asyncio` or `brief`. It did not chase calls into other modules. A renderer that
  reaches a clock through a callee would refute "pure", and that renderer stays in `bus`.
- **A cycle appears between the text modules and `bus`.** The edge scan says the graph is acyclic
  with `_REVERSIBLE_SAID` moved. A missed reference (a name read through `globals()`, or a
  string) would show up as an `ImportError` at the step that adds it.
- **The SDK registers something other than name, description, schema and the handler's
  arguments.** Read at 0.2.167: the wire entry is `name`, `description`, `inputSchema`,
  `annotations` (None from `tool()`), and `_meta`. The handler's `__name__` is never read. A
  different SDK version must be re-read.
- **B6's channel package claims the peer words.** B6 gives inter-lead channels one owning
  package, and `peertext` is a candidate for it. If B6 lands first and takes them, `peertext` goes
  there instead. The same holds for B5's `board/` and the three board-side text modules.

**Not run:**
- No code was moved or written.
- The order pin, the reply corpus pin and the verbatim AST check are specified and not written.
- `make check` and mypy were not run; only the six test files in "Baseline" were.
- Whether tools/list order affects a model was not measured.
- The relayed per-group sizes were not reproduced; see §1.
- The scan scripts ran from `$TMPDIR` and are not kept in the tree. The method is in §1, and a
  builder regenerates the tables by walking `bus.py`'s top level and `build_server`'s body with
  `ast`.

## Open

- **D-B7a: per-domain handler modules.** Stage 2 leaves about 2,200 lines in `bus`. The handlers
  could follow `commandbus` further, into per-domain modules each exporting its `TOOLS`. That
  needs the call layer (`_sender`, `_park`, `_outcome`, the argument readers and the five board
  reads) in a shared module the domain modules import.
  - For: the plan's per-build-step file split, which keeps parallel builders off the same file.
  - Against: `bus.py` has 19 commits since 2026-08-01 (run, `git log`; churn.csv's 18 predates
    Round 2), well below `app.py`'s 53, and the split adds a module boundary inside every
    handler's call path.
  - Recommendation: do not decide until stages 1 and 2 have landed and a board has actually
    contended on `bus.py`.
- **`commandbus`'s reply words.** About 640 lines of `commandbus` are its reply text, and they
  would fit the same split (`commandreplies`, or into `commandtext`). That is out of B7's scope as
  written. It is recorded here so that the asymmetry with the bus is a choice and not an
  oversight.
