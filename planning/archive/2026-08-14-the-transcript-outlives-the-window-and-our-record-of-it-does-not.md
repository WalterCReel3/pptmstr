# The transcript outlives the window; our record of it does not

**Dated:** 2026-08-14 · **Status:** open, not started ·
**Source:** a read of `instructkr/claw-code` at `7030d26` (`~/Source/claw-code`), a
clean-room reimplementation of the Claude Code harness — ~34K lines of Rust under
`rust/crates/`, plus a Python tree that is a file-path inventory rather than a port

Nothing below is quoted from that repository. It is a clean-room reimplementation of
source that leaked on 2026-03-31, and its own README records removing the leaked
snapshot over exactly that question; every item here is a design observation
re-derived against our tree, which is the only form in which it is safe or useful.

---

## Why most of it does not transfer

claw-code rebuilds the runtime. We rent it. That single difference decides which of
its answers are worth anything here, and it disqualifies most of them.

| layer | claw-code | pptmstr |
|---|---|---|
| API client, SSE framing | own, `api/src/sse.rs` | SDK |
| tool loop, tool specs | own, `tools/src/lib.rs` | SDK |
| session file | own, `runtime/src/session.rs` | SDK / CLI |
| compaction | own, `runtime/src/compact.rs` (702 lines, and **nothing auto-calls it** — only `/compact`) | SDK |
| sandbox | own, `runtime/src/sandbox.rs` | not attempted |
| permission model | own, `runtime/src/permissions.rs` | ours — `approval.py` |
| operator gate, review queue, edit-then-approve | **absent** | ours — the product |

Six of seven rows are questions we do not ask. The seventh is the one this note is
about, and the useful parts of the read are all in that row or below it: they concern
state **we** own, which is exactly the state that does not survive the window
closing.

Worth recording as a negative result, because it is the reason not to read further:
its sub-agents run with no prompter at all, so approval inside a sub-agent is
structurally impossible there. The thing we would most want to compare notes on is
the thing it does not have.

---

## 1. The transcript is already durable; nothing here reads it back

`AgentSession` mints `str(uuid.uuid4())` (`driver.py:525`) and hands it to the SDK as
`session_id` (`driver.py:901-929`). That call asks for neither `resume` nor a
`session_store`. Nothing in `pptmstr/` writes session state either — the only file
writes in the package are `settings.save` (`settings.py:106`) and the theme assets;
`approval.py:201` reads, and only to build a diff.

But the CLI writes a JSONL transcript per session id under a cwd slug whether we ask
or not. Measured:

```sh
find ~/.claude/projects/-home-wreel-Source-pptmstr -name '*.jsonl' | wc -l   # 149
```

So closing the window does not lose the conversation. It loses **our record of it**:
which sessions existed, what they cost, what the board said, which concerns were
delivered, and which of them the operator rewrote in flight. That last one is the
audit trail concerns were made store objects to preserve
(`2026-08-12-the-board-has-no-surface.md`), and it is the part with no backing store
at all.

**What claw-code contributes is one rule, not a format.** Its usage tracker rebuilds
cumulative tokens by re-walking the message list, and "have we compacted before" is
re-derived by sniffing the first system message rather than stored anywhere. Resume
cannot desync from a counter file because there is no counter file. That is
`STYLE.md` §1's *derive; do not store* applied across a restart boundary rather than
within a frame, and it is the rule that decides the shape of any store we add: persist
the intent stream, derive `UsageRollup`, `needs_you` and board state from it on load.

**Do not copy its storage.** `Session::save_to_path` (`session.rs:92`) rewrites the
whole JSON document after every turn — O(n²), no fsync, no lock. With N concurrent
agents that is not a slow path, it is a corruption path. `settings.save` already has
the right primitive here: temp file in the same directory, `os.replace`d over the
target (`settings.py:106-126`).

**And the SDK already offers the mechanism claw-code hand-rolled.** `SessionStore`
(`types.py:1463`) is a protocol with `append` (`:1485`), `load` for resume
materialization (`:1507`), and `list_subkeys` so sub-agent transcripts come back too
(`:1572`) — wired through `ClaudeAgentOptions.session_store` (`:2097`), with `resume`
(`:1827`) and `fork_session` (`:1980`). `append` is called *after* the subprocess's
local write succeeds, so durability is not ours to guarantee and a failing store
degrades to a `MirrorErrorMessage` rather than to data loss.

That splits the work in two, and only the first half is forced:

- **Our store's own state** — records, rollups, concerns, tasks, and the operator's
  edits. The SDK will never carry these; they are ours whatever we decide.
- **Reconnecting to a conversation** — `resume` plus the existing session id. Cheap
  to try, and it is what makes a restored card mean something rather than being a
  headstone.

The design question worth settling first is **what a restored session is**. A card
whose agent is not running is a state the rail has no vocabulary for, and inventing
one is a bigger change than the persistence itself.

## 2. Permission is one axis where the roles already need two

`classify()` (`approval.py:92`) maps a tool name to one of three dispositions,
fleet-wide and identically for every session. Separately, `templates.py` restricts
roles by tool list (`READ_ONLY_TOOLS`, `templates.py:40`). Two mechanisms, two
modules — and they do not mean the same thing by "read-only":

| | `_AUTO` (`approval.py:37`) | `READ_ONLY_TOOLS` (`templates.py:40`) |
|---|---|---|
| `Read`, `Glob`, `Grep`, `NotebookRead` | yes | yes |
| `TodoWrite`, `ListMcpResources`, `ReadMcpResource` | yes | no |
| `WebFetch`, `WebSearch` | **no** — in `_REVIEW` (`:51`) | **yes** |

Both are right under their own definition. `_AUTO`'s comment is "Cheap, reversible,
and they do not leave the box"; `READ_ONLY_TOOLS`' is "Enough to inspect a codebase
without changing it". One axis is **mutation**, the other is **reach**. A reviewer is
read-only in the second sense and not the first.

This is not a live defect — the gate wins, so a reviewer's `WebFetch` parks like
anything else. It is that two definitions of read-only exist, neither names its axis,
and the design that would need them to agree is the one we are heading toward: a
per-session trust level, so a reviewer session can be constrained without constraining
the fleet.

claw-code's shape is the one to take: each tool declares a required capability, the
session carries an active mode, and `authorize()` compares the two
(`permissions.rs:89-97`).

**Take its bug with it, because the bug is the design lesson.** `PermissionMode`
(`permissions.rs:4`) derives `Ord` over `ReadOnly < WorkspaceWrite < DangerFullAccess
< Prompt < Allow`, and `authorize` allows whenever `current_mode >= required_mode`
(`:97`). `Prompt` therefore outranks `DangerFullAccess` and **prompt mode
auto-allows everything**; the prompter is reachable only through a hardcoded
`WorkspaceWrite → DangerFullAccess` escalation. One ordered enum was made to carry
both *how much capability* and *what to do when it is exceeded*, and the second
meaning has no ordering.

So: `Disposition` stays the interaction axis, capability becomes a separate field,
and the two are never merged into one scale.

Unknown tools fail closed in both designs — `required_mode_for` defaults to the
strictest requirement (`permissions.rs:81-85`), `classify` returns
`REQUIRE_APPROVAL` (`approval.py:104-105`). Convergent; nothing to take.

## 3. Two lists that must differ, pinned by nothing

`STYLE.md` §3 already names this smell and already has an instance: the bus server
name is duplicated into `approval.py` deliberately, to keep the policy free of SDK
imports, **and pinned by a test** (`test_bus.py:633`, `test_templates.py:172`).

`READ_ONLY_TOOLS` and `_AUTO` are the same shape of duplication with nothing pinning
them. Checked:

```sh
grep -rn "READ_ONLY_TOOLS" tests/    # no matches
```

claw-code's answer is one table with three projections — wire definitions
(`tools/src/lib.rs:141`), permission specs (`:165`), and dispatch (`:188`), all
filtered by the same allowlist, so the tool table and the permission table cannot
drift because there is only one table.

**We cannot adopt that wholesale**, and the reason is already recorded: `approval.py`
is deliberately SDK-free, and a single registry feeding both the gate's policy and the
SDK's `AgentDefinition` tool lists would drag the SDK into the policy module and undo
that. The smaller move is the one `STYLE.md` already prescribes for the case it
caught — a test that fails when the two lists drift, plus a line on each naming its
axis, so the next reader knows the difference is intended.

## 4. A capability requested and not delivered should carry its reason

claw-code's `SandboxStatus` (`sandbox.rs:53`) keeps *requested*, *supported* and
*active* as separate fields, plus a `fallback_reason` (`:67`) when they disagree.

The instinct is that this is our "panes say what they cannot show", generalised. It
mostly is not, and the reason is worth writing down so it is not re-proposed:

**Most of ours are derivable, and a stored reason would be the duplicate `STYLE.md`
§1 forbids.** The sub-agent notice is computed at the draw site from `node_id[1] is
not None` (`transcript_pane.py:280-283`). A `fallback_reason` field carrying "sub-agent
output does not stream" would be a stored copy of a fact the snapshot already implies,
kept true by whoever remembers.

The pattern earns a field only where the reason is **not** derivable — a capability
the driver asked for and the runtime declined, where only the driver saw why. There is
one live candidate: `thinking={"type": "adaptive", "display": "summarized"}` is
requested at `driver.py:901-929`, and the comment there records that every model in
the launcher except Haiku defaults to `omitted`, which still emits thinking blocks
containing empty strings. If that ever silently degrades, the reasoning toggle filters
an empty set and the pane has nothing to say about why.

Not worth building now. Recorded so the next empty-reasoning report has somewhere to
land instead of being diagnosed from scratch.

---

## What we deliberately do not take

| | why |
|---|---|
| The hook exit-code contract (`0` allow / `2` deny / else warn-and-proceed, JSON on stdin) | A clean contract, and it buys us nothing the in-process gate does not already have. Its hooks cannot mutate tool input, so it cannot express edit-then-approve — the interaction this project exists for |
| Denial-as-tool-result | We already do it: rejection returns `permissionDecisionReason` and the agent reasons about what to try instead. Convergent, not borrowed |
| `content_block_stop` as the commit point for a tool call | Correct, and the SDK already gives it to us — `PreToolUse` fires on a complete block |
| Compaction | Ours is observed via `PreCompact` and counted (`model.py:169`). Theirs is 702 lines that only a slash command reaches |
| The `server` crate | Four routes, in-memory, no runtime, no approval endpoint. Remote access is a stated non-goal |
| The MCP client | 1,720 lines, wired to nothing, and framed with LSP-style `Content-Length` headers rather than newline-delimited JSON — it would not interoperate with a real MCP server |

---

## Verification boundary

**Executed against the working tree:** the `find` over `~/.claude/projects/` above
(149 files under the pptmstr slug); the `grep` for `READ_ONLY_TOOLS` in `tests/` (no
matches); a `grep` for file writes across `pptmstr/*.py`, which returns only
`settings.py` and `theme.py`'s asset path. Every `pptmstr` line number above was
resolved against the tree at the time of writing.

**Read, not run:** all claw-code claims — including the `Ord` bug, which is read off
`permissions.rs:4` and `:97` and has not been reproduced by building the binary. All
SDK claims are fresh reads of
`.venv/lib/python3.11/site-packages/claude_agent_sdk/types.py` against bundled CLI
2.1.226 (`_cli_version.py:3`).

**Not settled, and item 1 rests on it:** which of those 149 transcripts were written
by *our driver* rather than by interactive Claude Code sessions run in that directory.
The claim "our sessions are already on disk under the id we mint" is an inference from
the CLI's documented behaviour, not a measurement of ours. One probe closes it — launch
a real session, capture `AgentSession.session_id`, and assert a file named for it
appears under the slug for that session's `cwd`, not ours. Until it runs, item 1 is a
strong hypothesis and should not be built on.

A second, smaller unknown: whether `resume` accepts a session the SDK itself created
under our `session_id` without `fork_session` also being set. `types.py:1830-1834`
says `session_id` "cannot be used with `continue_conversation` or `resume` unless
`fork_session` is also set" — which reads as a constraint on the *resuming* call, and
would mean a restored session necessarily gets a new id. If so, the store must map
our stable record id to a changing SDK session id, and that is a design consequence,
not a detail.

## Sources

- `~/Source/claw-code` at `7030d26`, read 2026-08-14: `rust/crates/runtime/src/`
  (`permissions.rs`, `sandbox.rs`, `session.rs`, `compact.rs`, `hooks.rs`),
  `rust/crates/tools/src/lib.rs`, `rust/crates/server/src/lib.rs`, `PARITY.md`
- `claude_agent_sdk/types.py` at CLI 2.1.226 — `SessionStore` and the
  `ClaudeAgentOptions` resume/session-store fields
