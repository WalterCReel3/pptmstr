# Every bus tool declares its optional arguments as required

**Dated:** 2026-08-13 · **Status:** done, 2026-08-15 ·
**Found during:** the board-pane change (`2026-08-12-the-board-has-no-surface.md`)

Every handler in `bus.py` is written for optional arguments — `args.get(...)` with a
default, throughout — and every one of those arguments is emitted to the model as
**required**. The schema is checked before the handler runs, so the handler's
tolerance is never reached.

Two live reproductions, not inference. Both were hit while doing other work.

## 1. `claim_task` refuses the call its own description asks for

The tool describes itself as:

> Take a unit of work off the shared board. **Omit task_id** to be given the oldest
> task whose dependencies are all met.

Omitting it:

```
claim_task()
-> Input validation error: 'task_id' is a required property
```

The self-claiming model — the thing worth copying from agent teams, per
`TaskClaimRequested`'s docstring — is unreachable through the documented call. A
worker only gets work by passing `task_id=""`, which reaches
`str(args.get("task_id", "")).strip() or None` and coerces to "anything claimable".
That is an accident of the handler, not an interface.

## 2. `declare_task` refuses a call omitting `detail`

```
declare_task(task_id="t1", title="do t1", _from=[...])
-> Input validation error: 'detail' is a required property
```

The handler reads it as `args.get("detail", "")`. Caught by
`test_the_declare_handler_stamps_the_task_with_its_caller` on its first run, which
is how the second reproduction exists at all.

## Cause

The `{"name": type}` shorthand:

```python
@tool("claim_task", "... Omit task_id to ...", {"task_id": str})
@tool("declare_task", "...", {"task_id": str, "title": str, "detail": str, "depends_on": list})
```

puts **every** declared key into the generated schema's `required` list. Affected
today: `claim_task.task_id`, `declare_task.detail`, `declare_task.depends_on`.
`declare_task.task_id` is also required despite the handler generating one when it
is blank.

## Why it survived

There is no test anywhere that asserts anything about a bus tool's *schema*. The
handlers are tested by calling them directly with complete argument dicts, which
bypasses validation entirely — the same shape as the `_check_for_stranded_requests`
gap in STYLE.md §2, one layer out: the unit is covered and the contract is not.

It is also invisible from the model's side in the ordinary case, because a model
that fills in every field it is shown never trips it. It bites exactly the agent
that follows the instruction in the description.

## What the fix needs

Pass an explicit JSON-Schema dict with a real `required` list instead of the
shorthand. **This has not been checked against the SDK** — whether `@tool` accepts a
schema dict in place of the shorthand mapping, and in what shape, needs a fresh read
of `claude_agent_sdk`'s `tool`/`SdkMcpTool` rather than a recollection. That read is
the first step, not the last.

Then pin it: a test that asserts `required` for each bus tool, so the two spellings
of "optional" — the handler's default and the schema — cannot drift again. The
`approval.py` / `bus.py` name-list duplication already has exactly this treatment
and is the precedent to copy.

## What the SDK read established (2026-08-15, claude-agent-sdk 0.2.134)

`claude_agent_sdk.__init__._build_schema`, read in `.venv`, branches on the value
handed to `@tool`:

- A dict carrying a **string `type` and a `properties` key** is announced verbatim.
  A full JSON Schema is accepted, and `required` is exactly what it says — including
  absent.
- **Any other dict** is read as `{name: python_type}` and emits
  `"required": list(properties.keys())`. There is no spelling of "optional" in the
  shorthand at all; this is the cause, confirmed rather than inferred.
- A `TypedDict` takes `__required_keys__`, so `NotRequired[...]` would also have
  worked. Not used: an explicit schema keeps the contract next to the description
  that has to agree with it.

The silent part is the boundary between the first two branches. A hand-written dict
that misses either key falls back to the shorthand and marks everything required
again, with nothing raised — so `bus._schema` builds it, rather than each call site
spelling it out.

Also established, and not previously known here: the refusal does **not** come from
the CLI. `mcp.server.lowlevel.Server.call_tool` validates arguments against the
announced `inputSchema` with `jsonschema` before dispatching to the handler
(`server.py:534`), default on. The defect is therefore reproducible in-process, and
a test can meet the same validator a live call does.

## What shipped

Explicit schemas for the two tools that had an argument a caller may omit —
`claim_task.task_id`, and `declare_task`'s `task_id`, `detail` and `depends_on`,
with `title` still required. The other four keep the shorthand: `post_concern`,
`complete_task` and `release_task` need every argument they declare, and
`read_inbox` declares none, so there the shorthand tells the truth.

**The title of this doc overstates the defect.** Six tools were checked against
their announced schemas; two had optional arguments and both were wrong. "Every bus
tool" was never true.

Pinned by `tests/test_bus.py`: a table of the smallest call each description
licenses, validated against the announced schema; its counterweight, that an
argument a tool cannot work without is still refused when omitted (emptying every
`required` list would satisfy the first test alone); and `claim_task()` driven
through the real server to the intent, asserting an omitted `task_id` reaches the
handler as `None` rather than `""`. Mutation-tested: reverting `claim_task` to the
shorthand, emptying `required`, and dropping the handler's `or None` each fail the
matching test and nothing else.

## Left undone deliberately

`bus.py` answers a claim abandoned at shutdown with the same "the board is empty or
the remaining work is still blocked" text as a genuinely empty board, and
`read_inbox` says "Your inbox is empty" in both cases too. Both go through
`Bridge.ask`'s `on_abandon` fallback, which carries `task=None` / `concerns=()` and
is indistinguishable by value from a real answer. It is the STYLE.md §3 smell — one
message covering two situations — and it is not free: `abandon_all_requests` runs
*before* the grace window precisely so an agent can see the answer and unwind, so a
worker can be told the board is empty when the board is merely going away, and can
report that to its lead.

Not fixed here because the honest fix is in `effects.py`, marking the abandoned
answer at the effect so both tools can read it. The alternative that fits in
`bus.py` alone is comparing the returned effect against the fallback object by
identity, which works today only because `abandon_all_requests` passes the same
object through, and would revert to the wrong message silently if that ever became
a copy. Wants its own scope snapshot rather than being smuggled into a schema fix.

## Not in scope here, noted while adjacent

`ConcernWithdrawn` has no emitter anywhere in `pptmstr/` — the same shape as
`ConcernEdited` had before `2026-08-13`'s fix, and it was left deliberately.
Withdrawal is a real interaction question (who withdraws, and what a recipient that
has already read the concern sees) rather than a plumbing gap, so it wants its own
scope snapshot rather than a line in this one.
